"""What the bot shows: the main menu, the FAQ tree (draft and published versions) and response templates."""
from datetime import datetime
from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base
from backend.models.common import Json, created


class MenuItem(Base):
    __tablename__ = "menu_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(16))  # faq, support, tickets, language, faq_node, url, content
    value: Mapped[str] = mapped_column(String(512), default="")  # node id or https URL
    label: Mapped[dict] = mapped_column(Json, default=dict)  # per language; empty = the built-in label
    content: Mapped[dict] = mapped_column(Json, default=dict)  # rich content for action "content"
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class FaqNode(Base):
    """One question of the FAQ tree (the draft that staff edit). Users see the last published snapshot."""
    __tablename__ = "faq_nodes"
    __table_args__ = (Index("ix_faq_parent", "parent_id", "position"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("faq_nodes.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[dict] = mapped_column(Json, default=dict)  # button text per language
    content: Mapped[dict] = mapped_column(Json, default=dict)  # rich content: blocks and buttons
    keywords: Mapped[dict] = mapped_column(Json, default=dict)  # per language, for the free-text search
    # Where the answer leads: "" (show it), node (another question), url, support (open a ticket), menu.
    redirect: Mapped[str] = mapped_column(String(16), default="")
    redirect_value: Mapped[str] = mapped_column(String(512), default="")
    allow_ticket: Mapped[bool] = mapped_column(Boolean, default=True)  # «Contact support» under the answer
    department_id: Mapped[int | None] = mapped_column(Integer)  # tickets from this answer go there
    category_id: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = created()
    updated_by: Mapped[str | None] = mapped_column(String(64))


class FaqVersion(Base):
    __tablename__ = "faq_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot: Mapped[dict] = mapped_column(Json)
    note: Mapped[str] = mapped_column(String(255), default="")
    published_at: Mapped[datetime] = created()
    published_by: Mapped[str] = mapped_column(String(64), default="")


class Template(Base):
    """Saved replies for agents, per language."""
    __tablename__ = "templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    shortcut: Mapped[str] = mapped_column(String(32), default="")
    texts: Mapped[dict] = mapped_column(Json, default=dict)
    department_id: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = created()
