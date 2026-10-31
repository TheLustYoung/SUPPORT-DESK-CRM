"""User profiles and direct messages."""
import json
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from backend.api.deps import has, require, staff_required
from backend.api.tickets import message_row, ticket_row
from backend.database import session
from backend.models import MediaAsset, Message, Ticket, TgUser
from backend.services import tickets, users

router = APIRouter(prefix="/api/admin", tags=["users"])


@router.get("/users")
def list_users(q: str = "", tag: str = "", language: str = "", status: str = "", page: int = Query(1, ge=1),
               size: int = Query(50, ge=1, le=200), staff=Depends(staff_required), db=Depends(session)):
    sensitive = has(staff, "users.sensitive")
    rows, total = users.search(db, q, tag, language, status, page, size, sensitive=sensitive)
    tags = users.tags_of(db, [u.id for u in rows])
    return {"items": [{**users.summary(db, u, sensitive), "tags": tags.get(u.id, [])} for u in rows], "total": total, "page": page, "size": size}


@router.get("/users/{user_id}")
def get_user(user_id: int, staff=Depends(staff_required), db=Depends(session)):
    return users.profile(db, require(db, TgUser, user_id), has(staff, "users.sensitive"))


@router.get("/users/{user_id}/timeline")
def timeline(user_id: int, staff=Depends(staff_required), db=Depends(session)):
    return users.timeline(db, require(db, TgUser, user_id))


@router.get("/users/{user_id}/tickets")
def user_tickets(user_id: int, staff=Depends(staff_required), db=Depends(session)):
    user = require(db, TgUser, user_id)
    rows = db.scalars(select(Ticket).where(Ticket.user_id == user.id, tickets.visible(db, staff)).order_by(Ticket.id.desc())).all()
    return [ticket_row(t, user) for t in rows]


@router.get("/users/{user_id}/messages")
def user_messages(user_id: int, before: int | None = None, staff=Depends(staff_required), db=Depends(session)):
    """Message history of the user: direct messages and those of tickets the staff member may see."""
    user = require(db, TgUser, user_id)
    stmt = select(Message).where(Message.user_id == user.id, Message.direction.in_(("in", "out", "system")),
                                 (Message.ticket_id.is_(None)) | Message.ticket_id.in_(select(Ticket.id).where(tickets.visible(db, staff))))
    if before:
        stmt = stmt.where(Message.id < before)
    return [message_row(m) for m in db.scalars(stmt.order_by(Message.id.desc()).limit(100))]


class Tag(BaseModel):
    tag: str = Field(min_length=1, max_length=48)


@router.post("/users/{user_id}/tags")
def add_tag(user_id: int, data: Tag, staff=Depends(staff_required), db=Depends(session)):
    users.add_tag(db, require(db, TgUser, user_id), data.tag, staff)
    db.commit()
    return {"tags": users.tags_of(db, [user_id]).get(user_id, [])}


@router.delete("/users/{user_id}/tags/{tag}")
def remove_tag(user_id: int, tag: str, staff=Depends(staff_required), db=Depends(session)):
    users.remove_tag(db, require(db, TgUser, user_id), tag)
    db.commit()
    return {"tags": users.tags_of(db, [user_id]).get(user_id, [])}


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/users/{user_id}/notes")
def add_note(user_id: int, data: Note, staff=Depends(staff_required), db=Depends(session)):
    note = users.add_note(db, require(db, TgUser, user_id), data.text, staff)
    db.commit()
    return {"id": note.id, "author": note.staff_name, "text": note.text, "created_at": note.created_at}


class ContactDetails(BaseModel):
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    country: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)


@router.put("/users/{user_id}/contacts")
def set_contacts(user_id: int, data: ContactDetails, request: Request, staff=Depends(staff_required), db=Depends(session)):
    """Contacts the user gave in the conversation, written down by staff with users.sensitive; audited."""
    user = require(db, TgUser, user_id)
    users.set_contacts(db, user, data.model_dump(exclude_unset=True), source="staff", actor=staff, ip=request.state.ip)
    db.commit()
    return users.summary(db, user, True)


class Ban(BaseModel):
    reason: str = Field(default="", max_length=255)


@router.post("/users/{user_id}/ban")
def ban(user_id: int, data: Ban, request: Request, staff=Depends(staff_required), db=Depends(session)):
    users.ban(db, require(db, TgUser, user_id), staff, data.reason, request.state.ip)
    db.commit()
    return {"banned": True}


@router.post("/users/{user_id}/unban")
def unban(user_id: int, request: Request, staff=Depends(staff_required), db=Depends(session)):
    users.unban(db, require(db, TgUser, user_id), staff, request.state.ip)
    db.commit()
    return {"banned": False}


class Direct(BaseModel):
    text: str = Field(default="", max_length=4096)
    asset_id: int | None = None


@router.post("/users/{user_id}/message")
def direct(user_id: int, data: Direct, staff=Depends(staff_required), db=Depends(session)):
    asset = require(db, MediaAsset, data.asset_id) if data.asset_id else None
    message = users.direct_message(db, require(db, TgUser, user_id), staff, data.text, asset.id if asset else None,
                                   asset.kind if asset else "text", asset.filename if asset else None)
    db.commit()
    return message_row(message)


@router.get("/users/{user_id}/export")
def export(user_id: int, request: Request, staff=Depends(staff_required), db=Depends(session)):
    data = users.export(db, require(db, TgUser, user_id), staff, request.state.ip)
    db.commit()
    body = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    return Response(body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="user-{user_id}.json"'})


@router.post("/users/{user_id}/anonymize")
def anonymize(user_id: int, request: Request, staff=Depends(staff_required), db=Depends(session)):
    users.anonymize(db, require(db, TgUser, user_id), staff, request.state.ip)
    db.commit()
    return {"anonymized": True}
