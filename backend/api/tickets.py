"""Tickets in the dashboard: a unified list with search, filters and pages, the ticket card with its
whole conversation and history, replies with attachments, internal notes and every change of status, priority,
category, department and agent. Agents only ever see the tickets their role allows."""
from datetime import date, datetime, time, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from backend.api.deps import has, require, staff_required
from backend.database import session
from backend.models import MediaAsset, Message, Staff, Ticket, TicketEvent, TgUser
from backend.services import media, tickets, users
from backend.services.security import DomainError
from backend.services.tickets import Actor

router = APIRouter(prefix="/api/admin", tags=["tickets"])


def ticket_row(t: Ticket, user: TgUser | None) -> dict:
    return {"id": t.id, "number": t.number, "subject": t.subject, "status": t.status, "priority": t.priority,
            "department_id": t.department_id, "category_id": t.category_id, "assignee_id": t.assignee_id, "language": t.language,
            "source": t.source, "created_at": t.created_at, "updated_at": t.updated_at, "first_response_at": t.first_response_at,
            "resolved_at": t.resolved_at, "closed_at": t.closed_at, "due_response_at": t.due_response_at,
            "due_resolution_at": t.due_resolution_at, "response_breached": t.response_breached,
            "resolution_breached": t.resolution_breached, "reopen_count": t.reopen_count, "rating": t.rating,
            "rating_comment": t.rating_comment, "faq_node_id": t.faq_node_id, "last_user_message_at": t.last_user_message_at,
            "user": {"id": user.id, "name": user.full_name, "username": user.username, "banned": user.banned,
                     "bot_blocked": user.bot_blocked} if user else None}


def message_row(m: Message) -> dict:
    return {"id": m.id, "direction": m.direction, "author": m.author, "staff_id": m.staff_id, "kind": m.kind, "text": m.text,
            "file_name": m.file_name, "file_size": m.file_size, "has_file": bool(m.file_id or m.asset_id), "asset_id": m.asset_id,
            "delivery": m.delivery, "error": m.error, "created_at": m.created_at, "sent_at": m.sent_at, "ticket_id": m.ticket_id}


def _day(value: str | None, end: bool = False):
    if not value:
        return None
    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        raise HTTPException(422, "bad_date") from None
    return datetime.combine(day + timedelta(days=1) if end else day, time.min, tzinfo=timezone.utc)


@router.get("/tickets")
def list_tickets(q: str = "", status: str = "", stage: str = "", priority: str = "", department_id: str = "", assignee_id: str = "",
                 language: str = "", date_from: str = "", date_to: str = "", overdue: bool = False, mine: bool = False,
                 page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200), staff=Depends(staff_required), db=Depends(session)):
    stmt = select(Ticket).where(tickets.visible(db, staff))
    q = q.strip().lstrip("@")
    if q:
        matches = [Ticket.number.ilike(f"%{q}%"), Ticket.subject.ilike(f"%{q}%"),
                   Ticket.user_id.in_(select(TgUser.id).where(or_(TgUser.username.ilike(f"%{q}%"), TgUser.first_name.ilike(f"%{q}%"),
                                                                  TgUser.last_name.ilike(f"%{q}%")))),
                   Ticket.id.in_(select(Message.ticket_id).where(Message.text.ilike(f"%{q}%"), Message.direction != "note"))]
        if q.isdigit():
            matches += [Ticket.user_id == int(q), Ticket.id == int(q)]
        stmt = stmt.where(or_(*matches))
    if status:
        stmt = stmt.where(Ticket.status.in_(status.split(",")))
    if stage:
        stmt = stmt.where(Ticket.status.in_(tickets.keys_of(db, set(stage.split(",")))))
    if priority:
        stmt = stmt.where(Ticket.priority.in_(priority.split(",")))
    if department_id:
        stmt = stmt.where(Ticket.department_id.is_(None) if department_id == "none" else Ticket.department_id == int(department_id))
    if assignee_id:
        stmt = stmt.where(Ticket.assignee_id.is_(None) if assignee_id == "none" else Ticket.assignee_id == int(assignee_id))
    if mine:
        stmt = stmt.where(Ticket.assignee_id == staff.id)
    if language:
        stmt = stmt.where(Ticket.language == language)
    if date_from:
        stmt = stmt.where(Ticket.created_at >= _day(date_from))
    if date_to:
        stmt = stmt.where(Ticket.created_at < _day(date_to, end=True))
    if overdue:
        stmt = stmt.where(Ticket.status.notin_(tickets.keys_of(db, tickets.FINAL)), Ticket.response_breached | Ticket.resolution_breached)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Ticket.updated_at.desc(), Ticket.id.desc()).offset((page - 1) * size).limit(size)).all()
    people = {u.id: u for u in db.scalars(select(TgUser).where(TgUser.id.in_({t.user_id for t in rows})))} if rows else {}
    return {"items": [ticket_row(t, people.get(t.user_id)) for t in rows], "total": total, "page": page, "size": size}


def visible_ticket(db, staff: Staff, ticket_id: int) -> Ticket:
    ticket = require(db, Ticket, ticket_id)
    if not tickets.can_see(db, staff, ticket):
        raise HTTPException(404, "not_found")  # no hint that a hidden ticket exists
    return ticket


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int, staff=Depends(staff_required), db=Depends(session)):
    ticket = visible_ticket(db, staff, ticket_id)
    user = db.get(TgUser, ticket.user_id)
    directions = ("in", "out", "system", "note") if has(staff, "tickets.notes") else ("in", "out", "system")
    messages = db.scalars(select(Message).where(Message.ticket_id == ticket.id, Message.direction.in_(directions)).order_by(Message.id)).all()
    events = db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id).order_by(TicketEvent.id)).all()
    allowed = sorted(tickets.transitions(db).get(ticket.status, set()))
    others = db.scalars(select(Ticket).where(Ticket.user_id == ticket.user_id, Ticket.id != ticket.id, tickets.visible(db, staff))
                        .order_by(Ticket.id.desc()).limit(10)).all()
    return {**ticket_row(ticket, user), "messages": [message_row(m) for m in messages],
            "events": [{"id": e.id, "kind": e.kind, "old": e.old, "new": e.new, "actor_type": e.actor_type, "actor_name": e.actor_name,
                        "comment": e.comment, "created_at": e.created_at} for e in events],
            "allowed_statuses": allowed, "user_profile": users.summary(db, user, has(staff, "users.sensitive")) if user else None,
            "other_tickets": [{"id": t.id, "number": t.number, "status": t.status, "subject": t.subject, "created_at": t.created_at} for t in others]}


class NewTicket(BaseModel):
    user_id: int
    subject: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=4096)
    department_id: int | None = None
    category_id: int | None = None
    priority: str | None = None


@router.post("/tickets")
def create_ticket(data: NewTicket, request: Request, staff=Depends(staff_required), db=Depends(session)):
    """A ticket opened by staff for a user (e.g. after a phone call); the first message goes to the user."""
    user = require(db, TgUser, data.user_id)
    ticket, _ = tickets.create(db, user, language=users.language(db, user), department_id=data.department_id, category_id=data.category_id,
                               subject=data.subject, source="dashboard", actor=Actor.of(staff, request.state.ip), priority=data.priority)
    if data.text.strip():
        tickets.reply(db, ticket, staff, data.text)
    db.commit()
    return {"id": ticket.id, "number": ticket.number}


class Reply(BaseModel):
    text: str = Field(default="", max_length=4096)
    asset_id: int | None = None
    template_id: int | None = None


@router.post("/tickets/{ticket_id}/reply")
def reply(ticket_id: int, data: Reply, staff=Depends(staff_required), db=Depends(session)):
    ticket = visible_ticket(db, staff, ticket_id)
    asset = require(db, MediaAsset, data.asset_id) if data.asset_id else None
    message = tickets.reply(db, ticket, staff, data.text, asset_id=asset.id if asset else None, kind=asset.kind if asset else "text",
                            file_name=asset.filename if asset else None)
    db.commit()
    return message_row(message)


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/tickets/{ticket_id}/note")
def add_note(ticket_id: int, data: Note, staff=Depends(staff_required), db=Depends(session)):
    message = tickets.note(db, visible_ticket(db, staff, ticket_id), staff, data.text)
    db.commit()
    return message_row(message)


class Change(BaseModel):
    value: str | int | None = None
    comment: str = Field(default="", max_length=500)


@router.post("/tickets/{ticket_id}/status")
def set_status(ticket_id: int, data: Change, request: Request, staff=Depends(staff_required), db=Depends(session)):
    ticket = visible_ticket(db, staff, ticket_id)
    tickets.change_status(db, ticket, str(data.value or ""), Actor.of(staff, request.state.ip), comment=data.comment)
    db.commit()
    return {"status": ticket.status}


@router.post("/tickets/{ticket_id}/close")
def close(ticket_id: int, data: Change, request: Request, staff=Depends(staff_required), db=Depends(session)):
    ticket = visible_ticket(db, staff, ticket_id)
    tickets.staff_close(db, ticket, Actor.of(staff, request.state.ip), data.comment)
    db.commit()
    return {"status": ticket.status}


@router.post("/tickets/{ticket_id}/reopen")
def reopen(ticket_id: int, data: Change, request: Request, staff=Depends(staff_required), db=Depends(session)):
    ticket = visible_ticket(db, staff, ticket_id)
    tickets.staff_reopen(db, ticket, Actor.of(staff, request.state.ip), data.comment)
    db.commit()
    return {"status": ticket.status}


@router.post("/tickets/{ticket_id}/priority")
def set_priority(ticket_id: int, data: Change, request: Request, staff=Depends(staff_required), db=Depends(session)):
    ticket = visible_ticket(db, staff, ticket_id)
    tickets.set_priority(db, ticket, str(data.value or ""), Actor.of(staff, request.state.ip))
    db.commit()
    return {"priority": ticket.priority, "due_response_at": ticket.due_response_at, "due_resolution_at": ticket.due_resolution_at}


def _int(value) -> int | None:
    if value in (None, "", "none"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DomainError("bad_value") from None


@router.post("/tickets/{ticket_id}/category")
def set_category(ticket_id: int, data: Change, request: Request, staff=Depends(staff_required), db=Depends(session)):
    ticket = visible_ticket(db, staff, ticket_id)
    tickets.set_category(db, ticket, _int(data.value), Actor.of(staff, request.state.ip))
    db.commit()
    return {"category_id": ticket.category_id}


@router.post("/tickets/{ticket_id}/assign")
def assign(ticket_id: int, data: Change, request: Request, staff=Depends(staff_required), db=Depends(session)):
    ticket = visible_ticket(db, staff, ticket_id)
    tickets.assign(db, ticket, _int(data.value), Actor.of(staff, request.state.ip))
    db.commit()
    return {"assignee_id": ticket.assignee_id}


@router.post("/tickets/{ticket_id}/transfer")
def transfer(ticket_id: int, data: Change, request: Request, staff=Depends(staff_required), db=Depends(session)):
    ticket = visible_ticket(db, staff, ticket_id)
    tickets.transfer(db, ticket, _int(data.value), Actor.of(staff, request.state.ip), data.comment)
    db.commit()
    return {"department_id": ticket.department_id, "assignee_id": ticket.assignee_id, "category_id": ticket.category_id}


@router.get("/messages/{message_id}/file")
def message_file(message_id: int, staff=Depends(staff_required), db=Depends(session)):
    """An attachment, only for staff who may see its ticket (or the user's profile for direct messages)."""
    message = require(db, Message, message_id)
    if message.direction == "note" or (message.ticket_id and not tickets.can_see(db, staff, db.get(Ticket, message.ticket_id))) or \
            (not message.ticket_id and not has(staff, "users.view")):
        raise HTTPException(404, "not_found")
    if message.asset_id:
        asset = require(db, MediaAsset, message.asset_id)
        return FileResponse(media.path_of(asset), media_type=asset.mime, filename=asset.filename)
    path = media.attachment_path(message.id, message.file_name)
    if not path.exists():
        raise HTTPException(404, "file_not_copied")  # the worker copies it from Telegram shortly after it arrives
    return FileResponse(path, media_type="application/octet-stream", filename=message.file_name or path.name)
