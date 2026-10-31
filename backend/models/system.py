"""Staff accounts and roles, the audit log, notifications, settings, languages, bot texts, media and errors."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base
from backend.models.common import UTC, Json, created


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    permissions: Mapped[list] = mapped_column(Json, default=list)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(String(255), default="")


class Department(Base):
    """A support department or team: tickets are routed to it and its members see them."""
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[dict] = mapped_column(Json, default=dict)  # {lang: name}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created()


class StaffDepartment(Base):
    __tablename__ = "staff_departments"
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"), primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE"), primary_key=True)


class Staff(Base):
    __tablename__ = "staff"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1)  # bumped on password/role changes: old sessions end
    totp_secret: Mapped[str | None] = mapped_column(String(64))  # two-factor sign-in when set
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)  # linked with /link for Telegram notifications
    languages: Mapped[list] = mapped_column(Json, default=list)  # languages the agent speaks (auto-assignment)
    notify: Mapped[dict] = mapped_column(Json, default=dict)  # {"telegram": bool, "sound": bool}
    max_open_tickets: Mapped[int] = mapped_column(Integer, default=0)  # 0 = no limit for auto-assignment
    created_at: Mapped[datetime] = created()
    created_by: Mapped[str | None] = mapped_column(String(64))
    last_login_at: Mapped[datetime | None] = mapped_column(UTC)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTC)
    role: Mapped[Role] = relationship(lazy="joined")

    @property
    def name(self) -> str:
        return self.display_name or self.username

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset(self.role.permissions or []) if self.role else frozenset()


class AuditLog(Base):
    """Sensitive operations: permission changes, bans, exports, ticket status changes, configuration."""
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_created", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int | None] = mapped_column(Integer)
    staff_name: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(Json, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = created()


class StaffNotification(Base):
    __tablename__ = "staff_notifications"
    __table_args__ = (Index("ix_notifications_staff", "staff_id", "read", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))  # new_ticket, reply, assigned, overdue, escalated
    ticket_id: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(500))  # English, for exports; each person sees it translated from kind + data
    data: Mapped[dict] = mapped_column(Json, default=dict, server_default="{}")  # values of the text: number, user, subject…
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created()


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(Json)
    updated_at: Mapped[datetime] = created()


class Language(Base):
    __tablename__ = "languages"
    code: Mapped[str] = mapped_column(String(12), primary_key=True)  # IETF base code: en, ru, ar, pt-br
    name: Mapped[str] = mapped_column(String(64))  # in English, for staff
    native_name: Mapped[str] = mapped_column(String(64))  # shown to users
    flag: Mapped[str] = mapped_column(String(16), default="")
    rtl: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=100)


class BotText(Base):
    """A system message of the bot in one language, as edited in the dashboard (built-in texts are the fallback)."""
    __tablename__ = "bot_texts"
    __table_args__ = (UniqueConstraint("key", "lang"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64))
    lang: Mapped[str] = mapped_column(String(12))
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = created()
    updated_by: Mapped[str | None] = mapped_column(String(64))


class MediaAsset(Base):
    """A file uploaded in the dashboard for welcome messages, FAQ answers, campaigns or replies."""
    __tablename__ = "media_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # photo, video, audio, voice, document, animation
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(String(512))  # relative to STORAGE_DIR
    tg_file_id: Mapped[str | None] = mapped_column(String(255))  # cached after the first upload to Telegram
    created_at: Mapped[datetime] = created()
    created_by: Mapped[str | None] = mapped_column(String(64))


class ProcessedUpdate(Base):
    """Telegram update IDs already handled: a retried delivery of the same update is ignored."""
    __tablename__ = "processed_updates"
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = created()


class ErrorLog(Base):
    """Errors shown on the dashboard (delivery failures, crashed tasks, unhandled bot errors)."""
    __tablename__ = "error_log"
    __table_args__ = (Index("ix_errors_created", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(24))  # bot, worker, api, delivery
    kind: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = created()


class BotState(Base):
    """What the bot process last reported (heartbeat): shown on the System page."""
    __tablename__ = "bot_state"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="support")
    username: Mapped[str] = mapped_column(String(64), default="")
    mode: Mapped[str] = mapped_column(String(16), default="")  # polling, webhook
    last_seen: Mapped[datetime | None] = mapped_column(UTC)
