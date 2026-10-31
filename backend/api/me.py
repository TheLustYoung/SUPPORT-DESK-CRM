"""The signed-in staff member: profile, password, two-factor sign-in, Telegram notifications, notifications bell,
the overview page, global search and the lookups every page needs (departments, categories, statuses, agents)."""
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from backend.api.deps import staff_required
from backend.database import session
from backend.models import (BotState, Category, Department, Language, Staff, StaffNotification, Template, Ticket, TgUser)
from backend.services import analytics, audit, auth, options, team, tickets
from backend.services.auth import hasher
from backend.services.security import DomainError

router = APIRouter(prefix="/api/admin", tags=["me"])


@router.get("/me")
def me(staff=Depends(staff_required), db=Depends(session)):
    bot = db.get(BotState, "support")
    return {**team.public(db, staff), "permissions": sorted(staff.permissions), "role_name": staff.role.name if staff.role else "",
            "notify": staff.notify or {}, "company": options.get(db, "company_name"), "bot_username": bot.username if bot else ""}


class Profile(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    languages: list[str] | None = None
    notify: dict | None = None


@router.patch("/me")
def update_me(data: Profile, staff=Depends(staff_required), db=Depends(session)):
    if data.display_name is not None:
        staff.display_name = data.display_name.strip()
    if data.email is not None:
        staff.email = data.email.strip()
    if data.languages is not None:
        staff.languages = [code[:12] for code in data.languages][:30]
    if data.notify is not None:
        staff.notify = {"telegram": bool(data.notify.get("telegram", True)), "sound": bool(data.notify.get("sound", True))}
    db.commit()
    return me(staff, db)


class Password(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


@router.post("/me/password")
def change_password(data: Password, request: Request, staff=Depends(staff_required), db=Depends(session)):
    try:
        hasher.verify(staff.password_hash, data.current)
    except Exception:
        raise DomainError("wrong_password") from None
    problem = auth.password_problem(data.new)
    if problem:
        raise DomainError(problem)
    staff.password_hash, staff.session_version = hasher.hash(data.new), (staff.session_version or 1) + 1
    audit.record(db, staff, "auth.password", "staff", staff.username, {}, request.state.ip)
    db.commit()
    return {"ok": True, "signed_out": True}


@router.post("/me/totp")
def start_totp(staff=Depends(staff_required), db=Depends(session)):
    """Step 1 of two-factor sign-in: a new secret to scan in an authenticator app (kept until confirmed)."""
    secret = auth.new_totp()
    options.put(db, f"totp_pending:{staff.id}", secret)
    db.commit()
    return {"secret": secret, "uri": auth.totp_uri(secret, staff.username, str(options.get(db, "company_name")))}


class Code(BaseModel):
    code: str = Field(min_length=6, max_length=12)


@router.post("/me/totp/confirm")
def confirm_totp(data: Code, request: Request, staff=Depends(staff_required), db=Depends(session)):
    secret = options.get(db, f"totp_pending:{staff.id}")
    if not secret or not auth.totp_ok(secret, data.code):
        raise DomainError("code_wrong")
    staff.totp_secret = secret
    options.put(db, f"totp_pending:{staff.id}", None)
    audit.record(db, staff, "auth.totp_on", "staff", staff.username, {}, request.state.ip)
    db.commit()
    return {"totp": True}


@router.post("/me/totp/disable")
def disable_totp(data: Code, request: Request, staff=Depends(staff_required), db=Depends(session)):
    if not staff.totp_secret or not auth.totp_ok(staff.totp_secret, data.code):
        raise DomainError("code_wrong")
    staff.totp_secret = None
    audit.record(db, staff, "auth.totp_off", "staff", staff.username, {}, request.state.ip)
    db.commit()
    return {"totp": False}


@router.post("/me/telegram-code")
def telegram_code(staff=Depends(staff_required), db=Depends(session)):
    """The code to send to the bot as /link CODE to get notifications in Telegram."""
    bot = db.get(BotState, "support")
    return {"code": auth.link_code(staff), "bot_username": bot.username if bot else "", "valid_minutes": 10}


@router.post("/me/telegram-unlink")
def telegram_unlink(staff=Depends(staff_required), db=Depends(session)):
    staff.telegram_id = None
    db.commit()
    return {"telegram": False}


# ---------------------------------------------------------------- notifications, overview, search, lookups
@router.get("/notifications")
def notifications(after: int = 0, staff=Depends(staff_required), db=Depends(session)):
    rows = db.scalars(select(StaffNotification).where(StaffNotification.staff_id == staff.id, StaffNotification.id > after)
                      .order_by(StaffNotification.id.desc()).limit(50)).all()
    unread = db.scalar(select(StaffNotification.id).where(StaffNotification.staff_id == staff.id, StaffNotification.read.is_(False))
                       .order_by(StaffNotification.id.desc()).limit(1))
    count = len(db.scalars(select(StaffNotification.id).where(StaffNotification.staff_id == staff.id, StaffNotification.read.is_(False))
                           .limit(100)).all())
    return {"items": [{"id": n.id, "kind": n.kind, "ticket_id": n.ticket_id, "text": n.text, "data": n.data or {}, "read": n.read,
                       "created_at": n.created_at}
                      for n in rows], "unread": count, "latest_unread": unread}


class Read(BaseModel):
    ids: list[int] = Field(default_factory=list)  # empty: all


@router.post("/notifications/read")
def mark_read(data: Read, staff=Depends(staff_required), db=Depends(session)):
    stmt = update(StaffNotification).where(StaffNotification.staff_id == staff.id)
    if data.ids:
        stmt = stmt.where(StaffNotification.id.in_(data.ids))
    db.execute(stmt.values(read=True))
    db.commit()
    return {"ok": True}


@router.get("/overview")
def overview(staff=Depends(staff_required), db=Depends(session)):
    return analytics.overview(db, staff)


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=100), staff=Depends(staff_required), db=Depends(session)):
    """Global search: ticket number, Telegram ID, @username, name and conversation text."""
    text = q.strip().lstrip("@")
    result = {"tickets": [], "users": []}
    if "tickets.view" in staff.permissions:
        from backend.api.tickets import list_tickets
        result["tickets"] = list_tickets(q=text, page=1, size=8, staff=staff, db=db)["items"]
    if "users.view" in staff.permissions:
        conditions = [TgUser.username.ilike(f"%{text}%"), TgUser.first_name.ilike(f"%{text}%"), TgUser.last_name.ilike(f"%{text}%")]
        if text.isdigit():
            conditions.append(TgUser.id == int(text))
        result["users"] = [{"id": u.id, "name": u.full_name, "username": u.username}
                           for u in db.scalars(select(TgUser).where(or_(*conditions)).order_by(TgUser.last_seen.desc()).limit(8))]
    return result


@router.get("/lookups")
def lookups(staff=Depends(staff_required), db=Depends(session)):
    agents = [s for s in db.scalars(select(Staff).where(Staff.active.is_(True), ~Staff.username.contains(team.DELETED)))
              if "tickets.reply" in s.permissions]
    return {"departments": [{"id": d.id, "name": d.name, "enabled": d.enabled} for d in db.scalars(select(Department).order_by(Department.position))],
            "categories": [{"id": c.id, "name": c.name, "department_id": c.department_id, "enabled": c.enabled}
                           for c in db.scalars(select(Category).order_by(Category.position))],
            "statuses": [{"key": s.key, "name": s.name, "color": s.color, "stage": s.stage, "enabled": s.enabled}
                         for s in tickets.statuses(db).values()],
            "priorities": list(tickets.PRIORITIES),
            "languages": [{"code": lang.code, "name": lang.name, "native_name": lang.native_name, "flag": lang.flag, "rtl": lang.rtl,
                           "enabled": lang.enabled, "is_default": lang.is_default}
                          for lang in db.scalars(select(Language).order_by(Language.position))],
            "agents": [{"id": a.id, "name": a.name, "languages": a.languages or []} for a in agents],
            "templates": [{"id": t.id, "name": t.name, "shortcut": t.shortcut, "texts": t.texts, "department_id": t.department_id}
                          for t in db.scalars(select(Template).where(Template.enabled.is_(True)).order_by(Template.position))]
            if {"tickets.reply", "templates.edit"} & staff.permissions else [],
            "open_tickets": None if "tickets.view" not in staff.permissions else len(db.scalars(select(Ticket.id).where(
                tickets.visible(db, staff), Ticket.status.notin_(tickets.keys_of(db, tickets.FINAL)))).all())}
