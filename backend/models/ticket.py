"""Tickets: categories, statuses and permitted transitions, SLA targets, routing rules, history, messages."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base
from backend.models.common import UTC, Json, created


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    name: Mapped[dict] = mapped_column(Json, default=dict)
    default_priority: Mapped[str] = mapped_column(String(12), default="normal")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class TicketStatus(Base):
    __tablename__ = "ticket_statuses"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[dict] = mapped_column(Json, default=dict)  # per language, users see it in «My tickets»
    color: Mapped[str] = mapped_column(String(16), default="#64748b")
    # What the status means for routing, SLA and reports. Custom statuses pick one of the stages, so reports and
    # SLA keep working whatever the administrator renames or adds.
    stage: Mapped[str] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_user: Mapped[bool] = mapped_column(Boolean, default=False)  # tell the user when a ticket enters it


class StatusTransition(Base):
    __tablename__ = "status_transitions"
    from_key: Mapped[str] = mapped_column(ForeignKey("ticket_statuses.key", ondelete="CASCADE"), primary_key=True)
    to_key: Mapped[str] = mapped_column(ForeignKey("ticket_statuses.key", ondelete="CASCADE"), primary_key=True)


class SlaPolicy(Base):
    __tablename__ = "sla_policies"
    priority: Mapped[str] = mapped_column(String(12), primary_key=True)  # low, normal, high, urgent
    first_response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_minutes: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, default=0)


class AssignmentRule(Base):
    """Routing: the first enabled rule matching a new ticket picks its department, priority and/or agent."""
    __tablename__ = "assignment_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # conditions (None = any)
    department_id: Mapped[int | None] = mapped_column(Integer)
    category_id: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(12))
    priority: Mapped[str | None] = mapped_column(String(12))
    # actions
    set_department_id: Mapped[int | None] = mapped_column(Integer)
    set_priority: Mapped[str | None] = mapped_column(String(12))
    assign_to: Mapped[int | None] = mapped_column(Integer)  # one agent
    strategy: Mapped[str] = mapped_column(String(16), default="none")  # none, least_loaded, round_robin (department)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (Index("ix_tickets_user", "user_id", "id"), Index("ix_tickets_status", "status"),
                      Index("ix_tickets_assignee", "assignee_id", "status"), Index("ix_tickets_created", "created_at"),
                      Index("ix_tickets_department", "department_id", "status"), Index("ix_tickets_bot", "bot_id", "status"))
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str | None] = mapped_column(String(20), unique=True)  # shown to users: e.g. T-000123
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    department_id: Mapped[int | None] = mapped_column(Integer)
    category_id: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(12), default="normal")
    status: Mapped[str] = mapped_column(String(32), default="new")
    assignee_id: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(12), default="en")
    subject: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(16), default="bot")  # bot, dashboard
    # tickets of several bots and other channels (web chat, e-mail…) in one interface. Today: "main" / "telegram".
    bot_id: Mapped[str] = mapped_column(String(32), default="main", server_default="main")
    channel: Mapped[str] = mapped_column(String(16), default="telegram", server_default="telegram")
    faq_node_id: Mapped[int | None] = mapped_column(Integer)  # the FAQ answer the user came from
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = created()
    first_response_at: Mapped[datetime | None] = mapped_column(UTC)
    resolved_at: Mapped[datetime | None] = mapped_column(UTC)
    closed_at: Mapped[datetime | None] = mapped_column(UTC)
    last_user_message_at: Mapped[datetime | None] = mapped_column(UTC)
    last_staff_message_at: Mapped[datetime | None] = mapped_column(UTC)
    due_response_at: Mapped[datetime | None] = mapped_column(UTC)
    due_resolution_at: Mapped[datetime | None] = mapped_column(UTC)
    sla_paused_at: Mapped[datetime | None] = mapped_column(UTC)  # SLA time stopped: waiting for the user
    sla_paused_seconds: Mapped[int] = mapped_column(Integer, default=0)  # all earlier pauses, added to the targets
    response_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)
    reminded_at: Mapped[datetime | None] = mapped_column(UTC)  # the user was reminded of this wait (reset on a new status)
    rating: Mapped[int | None] = mapped_column(Integer)  # 1–5 after resolution
    rating_comment: Mapped[str] = mapped_column(String(500), default="")
    dedupe_key: Mapped[str | None] = mapped_column(String(80), unique=True)  # one ticket per user message


class TicketEvent(Base):
    """History of a ticket: every status, priority, department and assignment change, with who did it and when."""
    __tablename__ = "ticket_events"
    __table_args__ = (Index("ix_ticket_events_ticket", "ticket_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))  # created, status, priority, department, category, assignee, rating, sla
    old: Mapped[str | None] = mapped_column(String(64))
    new: Mapped[str | None] = mapped_column(String(64))
    actor_type: Mapped[str] = mapped_column(String(12))  # staff, user, system
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    actor_name: Mapped[str] = mapped_column(String(64), default="")
    comment: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = created()


class Message(Base):
    """Every message between a user and the support team: in a ticket, direct (no ticket), an internal note or a
    system notification. Outgoing ones carry their delivery status."""
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_ticket", "ticket_id", "id"), Index("ix_messages_user", "user_id", "id"),
                      Index("ix_messages_delivery", "delivery", "created_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))
    direction: Mapped[str] = mapped_column(String(8))  # in, out, note, system
    channel: Mapped[str] = mapped_column(String(16), default="telegram", server_default="telegram")  # where it was written / goes
    staff_id: Mapped[int | None] = mapped_column(Integer)
    author: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(16), default="text")  # text, photo, video, voice, audio, document, ...
    text: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[str | None] = mapped_column(String(255))  # Telegram file of an incoming attachment
    asset_id: Mapped[int | None] = mapped_column(Integer)  # dashboard upload of an outgoing attachment
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_size: Mapped[int | None] = mapped_column(Integer)
    markup: Mapped[str] = mapped_column(String(16), default="")  # buttons under a system message: rating, reopen
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    delivery: Mapped[str] = mapped_column(String(12), default="")  # out: queued, sending, sent, failed, blocked
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(255), default="")
    dedupe_key: Mapped[str | None] = mapped_column(String(80), unique=True)  # chat:message id of incoming messages
    created_at: Mapped[datetime] = created()
    sent_at: Mapped[datetime | None] = mapped_column(UTC)
