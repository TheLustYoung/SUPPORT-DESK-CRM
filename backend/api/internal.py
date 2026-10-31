"""API used only by the bot process. Authenticated by
the service key; closed to the outside in Plesk. The bot holds no business logic: tickets, languages, limits and
content all come from here."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from backend.api.deps import bot_required, require
from backend.database import session
from backend.models import (BotState, Category, Department, MediaAsset, MenuItem, Message, ProcessedUpdate, Ticket, TgUser, now)
from backend.services import auth, content, faq, limits, media, options, tickets, users
from backend.services.i18n import pick
from backend.services.security import DomainError

router = APIRouter(prefix="/internal", tags=["internal"])
BOT_SETTINGS = ("company_name", "ask_department", "ask_category", "csat_enabled", "search_enabled", "ask_language_first",
                "business_hours", "offline_auto_reply", "max_attachment_mb", "reopen_days", "messages_per_minute", "privacy_notice",
                "privacy_url")
FILE_KINDS = {"photo", "video", "voice", "audio", "document", "animation", "video_note", "sticker"}


class Heartbeat(BaseModel):
    username: str = Field(default="", max_length=64)
    mode: str = Field(default="polling", max_length=16)


@router.post("/heartbeat")
def heartbeat(data: Heartbeat, _=Depends(bot_required), db=Depends(session)):
    state = db.get(BotState, "support") or BotState(id="support")
    state.username, state.mode, state.last_seen = data.username or state.username, data.mode, now()
    db.merge(state)
    db.commit()
    return {"content_version": int(options.get(db, "content_version") or 0)}


@router.get("/version")
def version(_=Depends(bot_required), db=Depends(session)):
    return {"content_version": int(options.get(db, "content_version") or 0)}


@router.get("/content")
def bundle(_=Depends(bot_required), db=Depends(session)):
    """Everything the bot shows, at one content version: the bot caches it and reloads when the version changes."""
    published = faq.latest(db)
    t = content.translator(db)
    return {
        "version": int(options.get(db, "content_version") or 0),
        "languages": [{"code": lang.code, "native_name": lang.native_name, "flag": lang.flag, "rtl": lang.rtl}
                      for lang in content.languages(db)],
        "default": t.default, "texts": [[lang, key, value] for (lang, key), value in t.custom.items()],
        "welcome": options.get(db, "welcome") or {},
        "menu": [{"id": m.id, "action": m.action, "value": m.value, "label": m.label or {}, "content": m.content or {}}
                 for m in db.scalars(select(MenuItem).where(MenuItem.enabled.is_(True)).order_by(MenuItem.position, MenuItem.id))],
        "faq": published.snapshot if published else {"nodes": {}, "children": {}},
        "departments": [{"id": d.id, "name": d.name} for d in db.scalars(select(Department).where(Department.enabled.is_(True))
                                                                         .order_by(Department.position, Department.id))],
        "categories": [{"id": c.id, "department_id": c.department_id, "name": c.name}
                       for c in db.scalars(select(Category).where(Category.enabled.is_(True)).order_by(Category.position, Category.id))],
        "statuses": {s.key: {"name": s.name, "stage": s.stage} for s in tickets.statuses(db).values()},
        "settings": {k: options.get(db, k) for k in BOT_SETTINGS},
    }


@router.get("/assets/{asset_id}")
def asset(asset_id: int, _=Depends(bot_required), db=Depends(session)):
    item = require(db, MediaAsset, asset_id)
    return FileResponse(media.path_of(item), media_type=item.mime, filename=item.filename,
                        headers={"X-Kind": item.kind, "X-File-Id": item.tg_file_id or ""})


class FileId(BaseModel):
    file_id: str = Field(max_length=255)


@router.post("/assets/{asset_id}/file-id")
def remember_file_id(asset_id: int, data: FileId, _=Depends(bot_required), db=Depends(session)):
    """Telegram's id of an uploaded media file: next time the bot sends it without uploading again."""
    require(db, MediaAsset, asset_id).tg_file_id = data.file_id
    db.commit()
    return {"ok": True}


class Update(BaseModel):
    update_id: int


@router.post("/update")
def seen_update(data: Update, _=Depends(bot_required), db=Depends(session)):
    """False when this Telegram update was already handled (Telegram retried it): the bot then ignores it."""
    try:
        db.add(ProcessedUpdate(update_id=data.update_id))
        db.commit()
        return {"new": True}
    except IntegrityError:
        db.rollback()
        return {"new": False}


class Identity(BaseModel):
    user_id: int
    username: str | None = Field(default=None, max_length=64)
    first_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    language_code: str | None = Field(default=None, max_length=16)
    is_premium: bool = False


def profile_of(db, user: TgUser, created: bool = False) -> dict:
    open_ticket = db.scalar(select(Ticket).where(Ticket.user_id == user.id, Ticket.status.notin_(tickets.keys_of(db, tickets.FINAL)))
                            .order_by(Ticket.updated_at.desc()).limit(1))
    return {"language": users.language(db, user), "created": created, "banned": user.banned, "opt_out": user.campaigns_opt_out,
            "chosen_language": bool(user.language), "open_ticket": {"id": open_ticket.id, "number": open_ticket.number} if open_ticket else None}


@router.post("/register")
def register(data: Identity, _=Depends(bot_required), db=Depends(session)):
    user, created = users.register(db, data.user_id, data.username, data.first_name, data.last_name, data.language_code, data.is_premium)
    if created:
        users.event(db, user.id, "start")
    db.commit()
    return profile_of(db, user, created)


class LanguageChoice(BaseModel):
    user_id: int
    language: str = Field(max_length=12)


@router.post("/language")
def set_language(data: LanguageChoice, _=Depends(bot_required), db=Depends(session)):
    chosen = users.set_language(db, require(db, TgUser, data.user_id), data.language)
    db.commit()
    return {"language": chosen}


class Event(BaseModel):
    user_id: int
    kind: str = Field(pattern="^(faq_open|search|search_miss|start|menu)$")
    ref_id: int | None = None
    data: dict = Field(default_factory=dict)


@router.post("/event")
def event(data: Event, _=Depends(bot_required), db=Depends(session)):
    users.event(db, data.user_id, data.kind, data.ref_id, {k: str(v)[:200] if not isinstance(v, list) else v[:20]
                                                           for k, v in list(data.data.items())[:10]})
    db.commit()
    return {"ok": True}


class Incoming(BaseModel):
    user_id: int
    kind: str = Field(default="text", max_length=16)
    text: str | None = Field(default=None, max_length=4096)
    file_id: str | None = Field(default=None, max_length=255)
    file_name: str | None = Field(default=None, max_length=255)
    file_size: int | None = None
    tg_message_id: int | None = None
    dedupe_key: str | None = Field(default=None, max_length=80)


def _check_message(db, user: TgUser, data: Incoming) -> None:
    if user.banned:
        raise DomainError("banned", 403)
    limits.rate_limit(f"user:{user.id}", int(options.get(db, "messages_per_minute") or 20), 60)
    if data.kind not in FILE_KINDS | {"text"}:
        raise DomainError("unsupported")
    if data.file_size and data.file_size > int(options.get(db, "max_attachment_mb") or 19) * 1_000_000:
        raise DomainError("file_too_large")


class NewTicket(Incoming):
    department_id: int | None = None
    category_id: int | None = None
    faq_node_id: int | None = None
    ticket_key: str | None = Field(default=None, max_length=70)  # chat:album or chat:message — one ticket per album


def ticket_brief(db, ticket: Ticket, lang: str) -> dict:
    status = db.get(tickets.TicketStatus, ticket.status)
    return {"id": ticket.id, "number": ticket.number, "status": ticket.status, "status_name": pick(status.name if status else {}, lang),
            "stage": status.stage if status else "open", "subject": ticket.subject, "created_at": ticket.created_at,
            "updated_at": ticket.updated_at, "rating": ticket.rating}


@router.post("/tickets")
def create_ticket(data: NewTicket, _=Depends(bot_required), db=Depends(session)):
    """«Contact support»: the ticket and the user's first message."""
    user = require(db, TgUser, data.user_id)
    _check_message(db, user, data)
    lang = users.language(db, user)
    key = data.ticket_key or data.dedupe_key
    ticket, created = tickets.create(db, user, language=lang, department_id=data.department_id, category_id=data.category_id,
                                     subject=(data.text or "").strip().splitlines()[0][:200] if data.text else "",
                                     faq_node_id=data.faq_node_id, dedupe_key=key and f"t:{key}")
    # every message is stored once (its own key), also the 2nd…10th photo of an album that opened the ticket
    tickets.add_incoming(db, ticket, user, kind=data.kind, text=data.text, file_id=data.file_id, file_name=data.file_name,
                         file_size=data.file_size, tg_message_id=data.tg_message_id, dedupe_key=data.dedupe_key,
                         notify_staff=not created)  # a new ticket is announced once, as «new ticket»
    db.commit()
    return {**ticket_brief(db, ticket, lang), "created": created}


class Owner(BaseModel):
    user_id: int


@router.post("/tickets/list")
def my_tickets(data: Owner, _=Depends(bot_required), db=Depends(session)):
    user = require(db, TgUser, data.user_id)
    lang = users.language(db, user)
    rows = db.scalars(select(Ticket).where(Ticket.user_id == user.id).order_by(Ticket.updated_at.desc()).limit(20)).all()
    return [ticket_brief(db, t, lang) for t in rows]


def own_ticket(db, ticket_id: int, user_id: int) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.user_id != user_id:
        raise HTTPException(404, "not_found")
    return ticket


@router.post("/tickets/{ticket_id}")
def ticket_detail(ticket_id: int, data: Owner, _=Depends(bot_required), db=Depends(session)):
    ticket = own_ticket(db, ticket_id, data.user_id)
    lang = users.language(db, db.get(TgUser, data.user_id))
    messages = db.scalars(select(Message).where(Message.ticket_id == ticket.id, Message.direction.in_(("in", "out")))
                          .order_by(Message.id.desc()).limit(5)).all()
    reopen_days = int(options.get(db, "reopen_days") or 0)
    return {**ticket_brief(db, ticket, lang), "reopen_days": reopen_days,
            "messages": [{"direction": m.direction, "author": m.author, "text": m.text, "kind": m.kind, "at": m.created_at}
                         for m in reversed(messages)]}


@router.post("/tickets/{ticket_id}/message")
def add_message(ticket_id: int, data: Incoming, _=Depends(bot_required), db=Depends(session)):
    """A follow-up from the user in their ticket (also reopens a resolved one, see tickets.add_incoming)."""
    user = require(db, TgUser, data.user_id)
    ticket = own_ticket(db, ticket_id, user.id)
    _check_message(db, user, data)
    if tickets.stage_of(db, ticket.status) == "closed":
        raise DomainError("ticket_closed")
    stored = tickets.add_incoming(db, ticket, user, kind=data.kind, text=data.text, file_id=data.file_id, file_name=data.file_name,
                                  file_size=data.file_size, tg_message_id=data.tg_message_id, dedupe_key=data.dedupe_key)
    db.commit()
    return {**ticket_brief(db, ticket, users.language(db, user)), "stored": stored is not None}


@router.post("/tickets/{ticket_id}/close")
def close(ticket_id: int, data: Owner, _=Depends(bot_required), db=Depends(session)):
    user = require(db, TgUser, data.user_id)
    ticket = own_ticket(db, ticket_id, user.id)
    done = tickets.user_close(db, ticket, user)
    db.commit()
    return {**ticket_brief(db, ticket, users.language(db, user)), "done": done,
            "ask_rating": done and bool(options.get(db, "csat_enabled")) and ticket.rating is None}


@router.post("/tickets/{ticket_id}/reopen")
def reopen(ticket_id: int, data: Owner, _=Depends(bot_required), db=Depends(session)):
    user = require(db, TgUser, data.user_id)
    ticket = own_ticket(db, ticket_id, user.id)
    result = tickets.user_reopen(db, ticket, user)
    db.commit()
    return {**ticket_brief(db, ticket, users.language(db, user)), "result": result}


class Rating(Owner):
    stars: int = Field(ge=1, le=5)


@router.post("/tickets/{ticket_id}/rate")
def rate(ticket_id: int, data: Rating, _=Depends(bot_required), db=Depends(session)):
    ticket = own_ticket(db, ticket_id, data.user_id)
    done = tickets.rate(db, ticket, data.user_id, data.stars)
    if done:
        users.event(db, data.user_id, "rating", ticket.id, {"stars": data.stars})
    db.commit()
    return {"done": done}


class OptOut(Owner):
    out: bool


@router.post("/announcements")
def announcements(data: OptOut, _=Depends(bot_required), db=Depends(session)):
    """/stop and /news: the user's choice about announcements."""
    user = require(db, TgUser, data.user_id)
    user.campaigns_opt_out = data.out
    users.event(db, user.id, "opt_out" if data.out else "opt_in")
    db.commit()
    return {"opt_out": user.campaigns_opt_out}


@router.post("/blocked")
def blocked(data: Owner, _=Depends(bot_required), db=Depends(session)):
    """The user blocked the bot (my_chat_member update): no more announcements, shown on the profile."""
    user = db.get(TgUser, data.user_id)
    if user:
        user.bot_blocked, user.bot_blocked_at = True, now()
        db.commit()
    return {"ok": True}


class Link(Owner):
    code: str = Field(max_length=32)


@router.post("/link")
def link(data: Link, _=Depends(bot_required), db=Depends(session)):
    """/link CODE from a staff member: their Telegram account gets dashboard notifications."""
    staff = auth.staff_by_link_code(db, data.code)
    if not staff:
        raise DomainError("link_bad")
    for other in db.scalars(select(tickets.Staff).where(tickets.Staff.telegram_id == data.user_id, tickets.Staff.id != staff.id)):
        other.telegram_id = None  # one Telegram account belongs to one staff member
    staff.telegram_id = data.user_id
    from backend.services import audit
    audit.record(db, staff, "auth.telegram_link", "staff", staff.username, {"telegram_id": data.user_id}, "telegram")
    db.commit()
    return {"name": staff.name}


# ---------------------------------------------------------------- contacts the user supplies
class Contacts(Owner):
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    clear: bool = False


@router.post("/contacts")
def contacts(data: Contacts, _=Depends(bot_required), db=Depends(session)):
    """The user shares their phone (Telegram «share contact», only their own) or types an e-mail, or deletes both.
    Nothing is collected without the user acting: a bot never sees these by itself."""
    user = require(db, TgUser, data.user_id)
    if data.clear:
        users.set_contacts(db, user, {"phone": None, "email": None}, source="user")
    else:
        users.set_contacts(db, user, {k: v for k, v in {"phone": data.phone, "email": data.email}.items() if v is not None}, source="user")
    db.commit()
    return {"phone": user.phone, "email": user.email}


@router.post("/contacts/get")
def contacts_of(data: Owner, _=Depends(bot_required), db=Depends(session)):
    user = require(db, TgUser, data.user_id)
    return {"phone": user.phone, "email": user.email}


# ---------------------------------------------------------------- staff answering from Telegram
STAFF_KINDS = {"text", "photo", "video", "voice", "audio", "document", "animation"}


class StaffTicket(BaseModel):
    telegram_id: int
    ticket_id: int
    mode: str = Field(default="reply", pattern="^(reply|note)$")


def staff_on_ticket(db, data: StaffTicket) -> tuple[tickets.Staff, Ticket]:
    """The linked, active staff member who may answer (or annotate) this ticket — the same checks as the dashboard."""
    staff = db.scalar(select(tickets.Staff).where(tickets.Staff.telegram_id == data.telegram_id, tickets.Staff.active.is_(True)))
    if staff is None:
        raise DomainError("not_staff", 403)
    ticket = db.get(Ticket, data.ticket_id)
    if ticket is None or not tickets.can_see(db, staff, ticket):
        raise DomainError("not_found", 404)
    if ("tickets.notes" if data.mode == "note" else "tickets.reply") not in staff.permissions:
        raise DomainError("no_permission", 403)
    return staff, ticket


@router.post("/staff/ticket")
def staff_ticket(data: StaffTicket, _=Depends(bot_required), db=Depends(session)):
    """What the agent sees before answering in Telegram: the ticket, the user and the last messages."""
    staff, ticket = staff_on_ticket(db, data)
    user = db.get(TgUser, ticket.user_id)
    status = db.get(tickets.TicketStatus, ticket.status)
    recent = db.scalars(select(Message).where(Message.ticket_id == ticket.id, Message.direction.in_(("in", "out")))
                        .order_by(Message.id.desc()).limit(3)).all()
    return {"id": ticket.id, "number": ticket.number, "subject": ticket.subject, "user": user.full_name if user else "",
            "language": ticket.language, "status": pick(status.name if status else {}, ticket.language),
            "closed": tickets.stage_of(db, ticket.status) == "closed",
            "messages": [{"direction": m.direction, "author": m.author, "text": (m.text or f"[{m.kind}]")[:300]} for m in reversed(recent)]}


class StaffMessage(StaffTicket):
    kind: str = Field(default="text", max_length=16)
    text: str | None = Field(default=None, max_length=4096)
    file_id: str | None = Field(default=None, max_length=255)
    file_name: str | None = Field(default=None, max_length=255)
    dedupe_key: str | None = Field(default=None, max_length=80)


@router.post("/staff/message")
def staff_message(data: StaffMessage, _=Depends(bot_required), db=Depends(session)):
    """An agent's answer (or internal note) written in Telegram: stored in the ticket and delivered like a dashboard reply."""
    staff, ticket = staff_on_ticket(db, data)
    if tickets.already_stored(db, data.dedupe_key):
        return {"number": ticket.number, "stored": False}
    if data.kind not in STAFF_KINDS or (data.mode == "note" and data.kind != "text"):
        raise DomainError("unsupported")
    if data.mode == "note":
        tickets.note(db, ticket, staff, data.text or "", dedupe_key=data.dedupe_key)
    else:
        tickets.reply(db, ticket, staff, data.text or "", kind=data.kind, file_id=data.file_id, file_name=data.file_name,
                      dedupe_key=data.dedupe_key)
    db.commit()
    return {"number": ticket.number, "stored": True}


class BotError(BaseModel):
    kind: str = Field(max_length=64)
    message: str = Field(max_length=1000)


@router.post("/error")
def report_error(data: BotError, _=Depends(bot_required), db=Depends(session)):
    from backend.services import audit
    audit.error(db, "bot", data.kind, data.message)
    db.commit()
    return {"ok": True}
