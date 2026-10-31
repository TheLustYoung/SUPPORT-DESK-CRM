"""Telegram users of the bot: profile, tags, internal notes and the activity timeline."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base
from backend.models.common import UTC, Json, created


class TgUser(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_last_seen", "last_seen"), Index("ix_users_username", "username"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)  # Telegram user ID
    # Which bot the user talks to. One bot today: "main" (BOT_ID).
    bot_id: Mapped[str] = mapped_column(String(32), default="main", server_default="main")
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    tg_language: Mapped[str | None] = mapped_column(String(16))  # from Telegram
    language: Mapped[str | None] = mapped_column(String(12))  # chosen in the bot
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = created()
    last_seen: Mapped[datetime] = created()
    interactions: Mapped[int] = mapped_column(Integer, default=0)
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    ban_reason: Mapped[str] = mapped_column(String(255), default="")
    banned_at: Mapped[datetime | None] = mapped_column(UTC)
    banned_by: Mapped[str | None] = mapped_column(String(64))
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # the user blocked the bot
    bot_blocked_at: Mapped[datetime | None] = mapped_column(UTC)
    campaigns_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)  # /stop: no announcements
    # Only when the user supplied them: Telegram never gives these to a bot by itself.
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str | None] = mapped_column(String(64))
    anonymized: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p) or (f"@{self.username}" if self.username else str(self.id))


class UserTag(Base):
    __tablename__ = "user_tags"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tag: Mapped[str] = mapped_column(String(48), primary_key=True)
    created_at: Mapped[datetime] = created()


class UserNote(Base):
    __tablename__ = "user_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    staff_id: Mapped[int | None] = mapped_column(Integer)
    staff_name: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class UserEvent(Base):
    """Activity timeline and analytics: start, faq_open, search, search_miss, ticket_created, language, ..."""
    __tablename__ = "user_events"
    __table_args__ = (Index("ix_events_user", "user_id", "id"), Index("ix_events_kind", "kind", "created_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(32))
    ref_id: Mapped[int | None] = mapped_column(Integer)  # FAQ node, ticket, campaign
    data: Mapped[dict] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = created()
