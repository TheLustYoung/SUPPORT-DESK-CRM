"""Bulk messages and announcements: one delivery row per recipient makes retries safe."""
from datetime import datetime
from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base
from backend.models.common import UTC, Json, created


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    bot_id: Mapped[str] = mapped_column(String(32), default="main", server_default="main")  # the bot that sends it
    content: Mapped[dict] = mapped_column(Json, default=dict)  # rich content, a text per language
    filters: Mapped[dict] = mapped_column(Json, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft, scheduled, running, paused, done, cancelled
    scheduled_at: Mapped[datetime | None] = mapped_column(UTC)
    started_at: Mapped[datetime | None] = mapped_column(UTC)
    finished_at: Mapped[datetime | None] = mapped_column(UTC)
    rate_per_second: Mapped[int] = mapped_column(Integer, default=20)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    recipients: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = created()


class CampaignDelivery(Base):
    __tablename__ = "campaign_deliveries"
    __table_args__ = (UniqueConstraint("campaign_id", "user_id"), Index("ix_deliveries_state", "campaign_id", "status", "next_attempt_at"),
                      Index("ix_deliveries_user", "user_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    language: Mapped[str] = mapped_column(String(12), default="en")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending, sending, sent, failed, blocked, skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTC)
    error: Mapped[str] = mapped_column(String(255), default="")
    sent_at: Mapped[datetime | None] = mapped_column(UTC)
