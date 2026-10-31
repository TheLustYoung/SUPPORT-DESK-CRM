"""Telegram users: registration from the bot, profile, tags, notes, bans, direct messages, export,
anonymization and the activity timeline."""
import re
from datetime import timedelta
from sqlalchemy import delete, func, or_, select, update
from backend.models import (CampaignDelivery, Message, Staff, Ticket, TicketEvent, TgUser, UserEvent, UserNote, UserTag, now)
from backend.services import audit, content, options, tickets
from backend.services.i18n import match
from backend.services.security import DomainError

TAG = re.compile(r"[\w.-]{1,48}\Z", re.UNICODE)
EVENT_KINDS = ("start", "faq_open", "search", "search_miss", "ticket_created", "language", "rating", "opt_out", "opt_in", "contacts")


def register(db, user_id: int, username: str | None = None, first_name: str | None = None, last_name: str | None = None,
             language_code: str | None = None, is_premium: bool = False) -> tuple[TgUser, bool]:
    """Creates or refreshes a user from Telegram's data; (user, created). Telegram may change names and usernames."""
    user, created = db.get(TgUser, user_id), False
    moment = now()
    if user is None:
        from backend.config import settings
        user, created = TgUser(id=user_id, first_seen=moment, bot_id=settings().bot_id), True
        db.add(user)
    if not user.anonymized:
        user.username = (username or None) and username[:64]
        user.first_name = (first_name or None) and first_name[:128]
        user.last_name = (last_name or None) and last_name[:128]
        if language_code:  # Telegram omits it sometimes: keep the one we know
            user.tg_language = language_code[:16]
        user.is_premium = bool(is_premium)
    user.last_seen = moment
    user.interactions = (user.interactions or 0) + 1
    if user.bot_blocked:  # the user wrote again: they unblocked the bot
        user.bot_blocked, user.bot_blocked_at = False, None
    db.flush()
    return user, created


def language(db, user: TgUser) -> str:
    """The chosen language if it is still enabled, else the Telegram language when supported, else the default."""
    codes = [lang.code for lang in content.languages(db)]
    return match(user.language, codes) or match(user.tg_language, codes) or content.default_language(db)


def set_language(db, user: TgUser, code: str) -> str:
    codes = [lang.code for lang in content.languages(db)]
    chosen = match(code, codes)
    if not chosen:
        raise DomainError("unknown_language")
    user.language = chosen
    event(db, user.id, "language", data={"language": chosen})
    return chosen


def event(db, user_id: int, kind: str, ref_id: int | None = None, data: dict | None = None) -> None:
    db.add(UserEvent(user_id=user_id, kind=kind, ref_id=ref_id, data=data or {}))


# ---------------------------------------------------------------- dashboard
def search(db, query: str = "", tag: str = "", language_code: str = "", status: str = "", page: int = 1, size: int = 50,
           sensitive: bool = False):
    """Users by Telegram ID, username, name and — for roles allowed to see them — the contacts they supplied."""
    stmt = select(TgUser)
    query = (query or "").strip().lstrip("@")
    if query:
        conditions = [TgUser.username.ilike(f"%{query}%"), TgUser.first_name.ilike(f"%{query}%"), TgUser.last_name.ilike(f"%{query}%")]
        if sensitive:
            conditions += [TgUser.email.ilike(f"%{query}%"), TgUser.phone.ilike(f"%{query}%")]
        if query.isdigit():
            conditions.append(TgUser.id == int(query))
        stmt = stmt.where(or_(*conditions))
    if tag:
        stmt = stmt.where(TgUser.id.in_(select(UserTag.user_id).where(UserTag.tag == tag)))
    if language_code:
        stmt = stmt.where(TgUser.language == language_code)
    if status == "banned":
        stmt = stmt.where(TgUser.banned.is_(True))
    elif status == "blocked":
        stmt = stmt.where(TgUser.bot_blocked.is_(True))
    elif status == "opt_out":
        stmt = stmt.where(TgUser.campaigns_opt_out.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(TgUser.last_seen.desc()).offset((max(page, 1) - 1) * size).limit(size)).all()
    return rows, total


def tags_of(db, user_ids: list[int]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    if user_ids:
        for row in db.scalars(select(UserTag).where(UserTag.user_id.in_(user_ids)).order_by(UserTag.tag)):
            result.setdefault(row.user_id, []).append(row.tag)
    return result


def summary(db, user: TgUser, sensitive: bool) -> dict:
    data = {"id": user.id, "username": user.username, "first_name": user.first_name, "last_name": user.last_name,
            "name": user.full_name, "tg_language": user.tg_language, "language": user.language, "is_premium": user.is_premium,
            "first_seen": user.first_seen, "last_seen": user.last_seen, "interactions": user.interactions, "banned": user.banned,
            "ban_reason": user.ban_reason, "banned_at": user.banned_at, "banned_by": user.banned_by, "bot_blocked": user.bot_blocked,
            "campaigns_opt_out": user.campaigns_opt_out, "anonymized": user.anonymized}
    if sensitive:  # contact details only for roles with users.sensitive
        data.update(email=user.email, phone=user.phone, country=user.country, timezone=user.timezone)
    return data


def profile(db, user: TgUser, sensitive: bool) -> dict:
    """Everything the dashboard shows on a user's profile."""
    stages = tickets.statuses(db)
    counts = {"total": 0, "open": 0, "resolved": 0, "closed": 0, "reopened": 0}
    for status, reopened, number in db.execute(select(Ticket.status, Ticket.reopen_count, func.count()).where(Ticket.user_id == user.id)
                                               .group_by(Ticket.status, Ticket.reopen_count)):
        stage = stages[status].stage if status in stages else "open"
        counts["total"] += number
        counts["resolved" if stage == "resolved" else "closed" if stage == "closed" else "open"] += number
        if reopened:
            counts["reopened"] += number
    durations = [(t.resolved_at - t.created_at).total_seconds() for t in
                 db.scalars(select(Ticket).where(Ticket.user_id == user.id, Ticket.resolved_at.is_not(None)))]
    faq_paths = db.execute(select(UserEvent.ref_id, func.count()).where(UserEvent.user_id == user.id, UserEvent.kind == "faq_open")
                           .group_by(UserEvent.ref_id).order_by(func.count().desc()).limit(10)).all()
    from backend.services import faq
    from backend.services.i18n import pick
    published, default = faq.latest(db), content.default_language(db)
    titles = {int(k): pick(v.get("title"), default, default) for k, v in (published.snapshot["nodes"] if published else {}).items()}
    notes = db.scalars(select(UserNote).where(UserNote.user_id == user.id).order_by(UserNote.id.desc())).all()
    campaigns = db.scalars(select(CampaignDelivery).where(CampaignDelivery.user_id == user.id).order_by(CampaignDelivery.id.desc()).limit(20)).all()
    return {**summary(db, user, sensitive), "tags": tags_of(db, [user.id]).get(user.id, []), "tickets": counts,
            "avg_resolution_seconds": round(sum(durations) / len(durations)) if durations else None,
            "faq": [{"node_id": node_id, "title": titles.get(node_id, f"#{node_id}"), "count": number} for node_id, number in faq_paths],
            "notes": [{"id": n.id, "author": n.staff_name, "text": n.text, "created_at": n.created_at} for n in notes],
            "campaigns": [{"campaign_id": d.campaign_id, "status": d.status, "sent_at": d.sent_at, "error": d.error} for d in campaigns],
            "messages": db.scalar(select(func.count()).select_from(Message).where(Message.user_id == user.id, Message.direction != "note"))}


def timeline(db, user: TgUser, limit: int = 200) -> list[dict]:
    """Chronological activity: bot events, tickets and messages."""
    items = [{"at": e.created_at, "type": "event", "kind": e.kind, "ref_id": e.ref_id, "data": e.data}
             for e in db.scalars(select(UserEvent).where(UserEvent.user_id == user.id).order_by(UserEvent.id.desc()).limit(limit))]
    items += [{"at": m.created_at, "type": "message", "kind": m.direction, "ref_id": m.ticket_id,
               "data": {"text": (m.text or "")[:200], "media": m.kind, "author": m.author}}
              for m in db.scalars(select(Message).where(Message.user_id == user.id, Message.direction.in_(("in", "out")))
                                  .order_by(Message.id.desc()).limit(limit))]
    return sorted(items, key=lambda i: i["at"], reverse=True)[:limit]


EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,24}\Z")
PHONE = re.compile(r"\+?[0-9][0-9 ()\-]{5,28}\Z")
COUNTRY = re.compile(r"[\w .'\-]{2,64}\Z", re.UNICODE)


def set_contacts(db, user: TgUser, values: dict, *, source: str, actor: Staff | None = None, ip: str = "") -> None:
    """Contact details the user supplied: shared or typed in the bot (`source="user"`), or written down by
    staff from the conversation (`source="staff"`, audited). Empty values erase. Telegram never gives them by itself."""
    if user.anonymized:
        raise DomainError("user_anonymized")
    checks = {"email": EMAIL, "phone": PHONE, "country": COUNTRY}
    changed = []
    for field in ("email", "phone", "country", "timezone"):
        if field not in values:
            continue
        value = (values[field] or "").strip() or None
        if value and field == "phone":
            value = re.sub(r"[ ()\-]", "", value)
            value = value if value.startswith("+") else "+" + value  # Telegram shares numbers without the plus
        if value and field in checks and not checks[field].match(value):
            raise DomainError(f"bad_{field}")
        if value and field == "timezone":
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise DomainError("bad_timezone") from None
        if getattr(user, field) != value:
            setattr(user, field, value)
            changed.append(field)
    if changed:
        event(db, user.id, "contacts", data={"fields": changed, "source": source})
        if actor is not None:
            audit.record(db, actor, "user.contacts", "user", user.id, {"fields": changed}, ip)


def add_tag(db, user: TgUser, tag: str, actor: Staff) -> None:
    tag = (tag or "").strip().lower()
    if not TAG.match(tag):
        raise DomainError("bad_tag")
    if db.get(UserTag, (user.id, tag)) is None:
        db.add(UserTag(user_id=user.id, tag=tag))


def remove_tag(db, user: TgUser, tag: str) -> None:
    db.execute(delete(UserTag).where(UserTag.user_id == user.id, UserTag.tag == tag))


def add_note(db, user: TgUser, text: str, actor: Staff) -> UserNote:
    text = (text or "").strip()
    if not text or len(text) > 4000:
        raise DomainError("empty_message")
    note = UserNote(user_id=user.id, staff_id=actor.id, staff_name=actor.username, text=text)
    db.add(note)
    return note


def ban(db, user: TgUser, actor: Staff, reason: str = "", ip: str = "") -> None:
    user.banned, user.ban_reason, user.banned_at, user.banned_by = True, (reason or "")[:255], now(), actor.username
    audit.record(db, actor, "user.ban", "user", user.id, {"reason": user.ban_reason}, ip)


def unban(db, user: TgUser, actor: Staff, ip: str = "") -> None:
    user.banned, user.ban_reason, user.banned_at, user.banned_by = False, "", None, None
    audit.record(db, actor, "user.unban", "user", user.id, {}, ip)


def direct_message(db, user: TgUser, actor: Staff, text: str, asset_id: int | None = None, kind: str = "text",
                   file_name: str | None = None) -> Message:
    """A message to a user without an open ticket; recorded with sender, time and delivery status."""
    if user.anonymized:
        raise DomainError("user_anonymized")
    text = tickets.check_text(text, bool(asset_id))
    return tickets.outgoing(db, user.id, None, actor, text, asset_id=asset_id, kind=kind, file_name=file_name, direction="out")


def export(db, user: TgUser, actor: Staff, ip: str = "") -> dict:
    """All data kept about a user, as one JSON document; the export itself is audited."""
    audit.record(db, actor, "user.export", "user", user.id, {}, ip)
    ticket_rows = db.scalars(select(Ticket).where(Ticket.user_id == user.id).order_by(Ticket.id)).all()
    ticket_ids = [t.id for t in ticket_rows]
    plain = lambda row, skip=(): {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name not in skip}
    return {"user": plain(user), "tags": [t.tag for t in db.scalars(select(UserTag).where(UserTag.user_id == user.id))],
            "notes": [plain(n) for n in db.scalars(select(UserNote).where(UserNote.user_id == user.id))],
            "tickets": [plain(t) for t in ticket_rows],
            "ticket_events": [plain(e) for e in db.scalars(select(TicketEvent).where(TicketEvent.ticket_id.in_(ticket_ids)))] if ticket_ids else [],
            "messages": [plain(m, ("dedupe_key",)) for m in db.scalars(select(Message).where(Message.user_id == user.id).order_by(Message.id))],
            "events": [plain(e) for e in db.scalars(select(UserEvent).where(UserEvent.user_id == user.id).order_by(UserEvent.id))],
            "campaigns": [plain(d) for d in db.scalars(select(CampaignDelivery).where(CampaignDelivery.user_id == user.id))],
            "exported_at": now(), "exported_by": actor.username}


def anonymize(db, user: TgUser, actor: Staff | None, ip: str = "") -> None:
    """Erases personal data (names, contacts, message texts and file references) and keeps the anonymous statistics."""
    user.username = user.first_name = user.last_name = user.email = user.phone = user.country = user.timezone = None
    user.anonymized, user.ban_reason = True, ""
    from backend.services import media
    for message in db.scalars(select(Message).where(Message.user_id == user.id, Message.file_id.is_not(None))):
        media.attachment_path(message.id, message.file_name).unlink(missing_ok=True)  # copies of their files too
    db.execute(update(Message).where(Message.user_id == user.id).values(text=None, file_id=None, file_name=None, author=""))
    db.execute(update(Ticket).where(Ticket.user_id == user.id).values(subject="", rating_comment=""))
    db.execute(delete(UserNote).where(UserNote.user_id == user.id))
    db.execute(delete(UserTag).where(UserTag.user_id == user.id))
    db.execute(update(UserEvent).where(UserEvent.user_id == user.id).values(data={}))
    audit.record(db, actor, "user.anonymize", "user", user.id, {}, ip)


def apply_retention(db) -> dict:
    """Retention rules (Settings → General): old analytics events are removed, long-inactive users anonymized."""
    result = {"events": 0, "users": 0}
    days = int(options.get(db, "retention_events_days") or 0)
    if days:
        result["events"] = db.execute(delete(UserEvent).where(UserEvent.created_at < now() - timedelta(days=days))).rowcount or 0
    days = int(options.get(db, "retention_inactive_users_days") or 0)
    if days:
        for user in db.scalars(select(TgUser).where(TgUser.anonymized.is_(False), TgUser.last_seen < now() - timedelta(days=days))).all():
            anonymize(db, user, None)
            result["users"] += 1
    return result
