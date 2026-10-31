"""What the bot shows, edited without code: the FAQ tree (draft → preview → publish, versions),
the welcome message, the main menu, the bot's system texts per language, the media library and response templates."""
import re
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from backend.api.deps import require, staff_required
from backend.config import settings
from backend.database import session
from backend.models import BotText, FaqVersion, MediaAsset, MenuItem, Template, now
from backend.services import audit, faq, media, options, queue, rich
from backend.services.i18n import builtin, builtin_keys
from backend.services.security import DomainError

router = APIRouter(prefix="/api/admin", tags=["content"])
MENU_ACTIONS = {"faq", "support", "tickets", "language", "contacts", "faq_node", "url", "content"}
PLACEHOLDER = re.compile(r"{(\w+)}")


# ---------------------------------------------------------------- FAQ
@router.get("/faq")
def get_faq(staff=Depends(staff_required), db=Depends(session)):
    latest = faq.latest(db)
    return {"nodes": [faq.node_dict(n) for n in faq.nodes(db)], "unpublished": faq.has_unpublished(db),
            "published": {"id": latest.id, "at": latest.published_at, "by": latest.published_by} if latest else None,
            "limits": {k: options.get(db, k) for k in ("faq_max_depth", "faq_max_children", "faq_max_nodes")}}


@router.get("/faq/versions")
def versions(staff=Depends(staff_required), db=Depends(session)):
    rows = db.scalars(select(FaqVersion).order_by(FaqVersion.id.desc()).limit(50)).all()
    return [{"id": v.id, "note": v.note, "published_at": v.published_at, "published_by": v.published_by,
             "questions": len(v.snapshot.get("nodes", {}))} for v in rows]


class NodeData(BaseModel):
    parent_id: int | None = None
    title: dict[str, str] | None = None
    content: dict | None = None
    keywords: dict[str, str] | None = None
    enabled: bool | None = None
    redirect: str | None = None
    redirect_value: str | None = None
    allow_ticket: bool | None = None
    department_id: int | None = None
    category_id: int | None = None


@router.post("/faq/nodes")
def create_node(data: NodeData, staff=Depends(staff_required), db=Depends(session)):
    node = faq.create(db, data.parent_id, data.model_dump(exclude_unset=True, exclude={"parent_id"}), staff.username)
    db.commit()
    return faq.node_dict(node)


@router.patch("/faq/nodes/{node_id}")
def update_node(node_id: int, data: NodeData, staff=Depends(staff_required), db=Depends(session)):
    node = faq.update(db, node_id, data.model_dump(exclude_unset=True, exclude={"parent_id"}), staff.username)
    db.commit()
    return faq.node_dict(node)


@router.delete("/faq/nodes/{node_id}")
def delete_node(node_id: int, staff=Depends(staff_required), db=Depends(session)):
    removed = faq.remove(db, node_id)
    db.commit()
    return {"removed": removed}


class Move(BaseModel):
    parent_id: int | None = None
    position: int = Field(ge=0)


@router.post("/faq/nodes/{node_id}/move")
def move_node(node_id: int, data: Move, staff=Depends(staff_required), db=Depends(session)):
    faq.move(db, node_id, data.parent_id, data.position, staff.username)
    db.commit()
    return {"ok": True}


class Order(BaseModel):
    parent_id: int | None = None
    ids: list[int]


@router.post("/faq/reorder")
def reorder(data: Order, staff=Depends(staff_required), db=Depends(session)):
    faq.reorder(db, data.parent_id, data.ids)
    db.commit()
    return {"ok": True}


class Publish(BaseModel):
    note: str = Field(default="", max_length=255)


@router.post("/faq/publish")
def publish(data: Publish, request: Request, staff=Depends(staff_required), db=Depends(session)):
    version = faq.publish(db, staff.username, data.note)
    audit.record(db, staff, "faq.publish", "faq_version", version.id, {"note": data.note}, request.state.ip)
    db.commit()
    return {"id": version.id}


@router.post("/faq/versions/{version_id}/restore")
def restore(version_id: int, request: Request, staff=Depends(staff_required), db=Depends(session)):
    faq.restore(db, version_id, staff.username)
    audit.record(db, staff, "faq.restore", "faq_version", version_id, {}, request.state.ip)
    db.commit()
    return {"ok": True}


class Preview(BaseModel):
    content: dict | None = None  # rich content to send as it is
    node_id: int | None = None  # or a FAQ question of the draft, with its sub-questions as buttons
    language: str = Field(default="en", max_length=12)


def _preview(data: Preview, staff, db):
    if not staff.telegram_id:
        raise DomainError("telegram_not_linked")
    if data.node_id is not None:
        require(db, faq.FaqNode, data.node_id)
    elif data.content is not None:
        try:
            rich.check_assets(db, rich.parse(data.content))
        except ValueError as error:
            raise DomainError(rich.reason(error)) from None
    else:
        raise DomainError("empty_message")
    queue.later(db, "workers.tasks.send_preview", staff.telegram_id, data.content, data.node_id, data.language)
    db.commit()
    return {"sent_to": "telegram"}


@router.post("/faq/preview")
def faq_preview(data: Preview, staff=Depends(staff_required), db=Depends(session)):
    """Sends the draft to the editor's own Telegram (Profile → Telegram notifications must be linked)."""
    return _preview(data, staff, db)


@router.post("/content/preview")
def content_preview(data: Preview, staff=Depends(staff_required), db=Depends(session)):
    return _preview(data, staff, db)


# ---------------------------------------------------------------- welcome message and main menu
@router.get("/content/welcome")
def get_welcome(staff=Depends(staff_required), db=Depends(session)):
    return options.get(db, "welcome") or {"blocks": [], "buttons": []}


@router.put("/content/welcome")
def put_welcome(data: dict, request: Request, staff=Depends(staff_required), db=Depends(session)):
    """Empty content = the built-in welcome text (translated in Bot texts)."""
    try:
        parsed = rich.parse(data)
        rich.check_assets(db, parsed)
    except ValueError as error:
        raise DomainError(rich.reason(error)) from None
    options.put(db, "welcome", parsed.model_dump())
    options.bump_content_version(db)
    audit.record(db, staff, "content.welcome", "content", "welcome", {}, request.state.ip)
    db.commit()
    return parsed.model_dump()


def menu_dict(item: MenuItem) -> dict:
    return {"id": item.id, "action": item.action, "value": item.value, "label": item.label or {}, "content": item.content or {},
            "enabled": item.enabled, "position": item.position}


@router.get("/content/menu")
def get_menu(staff=Depends(staff_required), db=Depends(session)):
    return [menu_dict(m) for m in db.scalars(select(MenuItem).order_by(MenuItem.position, MenuItem.id))]


class MenuEntry(BaseModel):
    id: int | None = None
    action: str
    value: str = Field(default="", max_length=512)
    label: dict[str, str] = Field(default_factory=dict)
    content: dict = Field(default_factory=dict)
    enabled: bool = True


@router.put("/content/menu")
def put_menu(items: list[MenuEntry], request: Request, staff=Depends(staff_required), db=Depends(session)):
    """The whole main menu in its order: items are added, removed, renamed, reordered, enabled or disabled."""
    if len(items) > 12:
        raise DomainError("too_many_items")
    existing = {m.id: m for m in db.scalars(select(MenuItem))}
    kept = set()
    for position, entry in enumerate(items):
        if entry.action not in MENU_ACTIONS:
            raise DomainError("bad_action")
        label = {k[:12]: v.strip()[:64] for k, v in entry.label.items() if v.strip()}
        if entry.action in ("faq_node", "url", "content") and not label:
            raise DomainError("button_label")  # custom items have no built-in name
        if entry.action == "url" and not entry.value.startswith(("https://", "http://", "tg://")):
            raise DomainError("bad_url")
        if entry.action == "faq_node" and (not entry.value.isdigit() or db.get(faq.FaqNode, int(entry.value)) is None):
            raise DomainError("bad_redirect")
        body = {}
        if entry.action == "content":
            try:
                parsed = rich.parse(entry.content)
                rich.check_assets(db, parsed)
            except ValueError as error:
                raise DomainError(rich.reason(error)) from None
            if parsed.empty:
                raise DomainError("empty_message")
            body = parsed.model_dump()
        item = existing.get(entry.id) if entry.id else None
        if item is None:
            item = MenuItem()
            db.add(item)
        item.action, item.value, item.label, item.content = entry.action, entry.value.strip(), label, body
        item.enabled, item.position = entry.enabled, position
        db.flush()
        kept.add(item.id)
    for item_id, item in existing.items():
        if item_id not in kept:
            db.delete(item)
    options.bump_content_version(db)
    audit.record(db, staff, "content.menu", "content", "menu", {"items": len(items)}, request.state.ip)
    db.commit()
    return get_menu(staff, db)


# ---------------------------------------------------------------- bot texts (translations of system messages)
@router.get("/content/texts")
def texts_overview(staff=Depends(staff_required), db=Depends(session)):
    keys = builtin_keys()
    custom = db.execute(select(BotText.lang, func.count()).group_by(BotText.lang)).all()
    return {"keys": keys, "builtin": {code: len([k for k in keys if values.get(k)]) for code, values in builtin().items()},
            "custom": dict(custom)}


@router.get("/content/texts/{lang}")
def texts_of(lang: str, staff=Depends(staff_required), db=Depends(session)):
    custom = {row.key: row.value for row in db.scalars(select(BotText).where(BotText.lang == lang))}
    english, own = builtin().get("en", {}), builtin().get(lang, {})
    return [{"key": k, "english": english.get(k, ""), "builtin": own.get(k, ""), "custom": custom.get(k)} for k in builtin_keys()]


@router.put("/content/texts/{lang}")
def save_texts(lang: str, values: dict[str, str | None], request: Request, staff=Depends(staff_required), db=Depends(session)):
    """Saves edited texts of one language; an empty value returns the key to the built-in text."""
    keys = set(builtin_keys())
    english = builtin().get("en", {})
    for key, value in values.items():
        if key not in keys:
            raise DomainError("unknown_key")
        row = db.scalar(select(BotText).where(BotText.key == key, BotText.lang == lang))
        value = (value or "").strip()
        if not value:
            if row:
                db.delete(row)
            continue
        if len(value) > 4096 or not rich.check_html(value):
            raise DomainError("bad_html")
        if not set(PLACEHOLDER.findall(value)) <= set(PLACEHOLDER.findall(english.get(key, ""))):
            raise DomainError("bad_placeholder")
        if row is None:
            db.add(BotText(key=key, lang=lang[:12], value=value, updated_at=now(), updated_by=staff.username))
        else:
            row.value, row.updated_at, row.updated_by = value, now(), staff.username
    options.bump_content_version(db)
    audit.record(db, staff, "content.texts", "language", lang, {"keys": len(values)}, request.state.ip)
    db.commit()
    return texts_of(lang, staff, db)


# ---------------------------------------------------------------- media library
def asset_dict(asset: MediaAsset) -> dict:
    return {"id": asset.id, "kind": asset.kind, "filename": asset.filename, "mime": asset.mime, "size": asset.size,
            "created_at": asset.created_at, "created_by": asset.created_by}


@router.get("/media")
def list_media(kind: str = "", staff=Depends(staff_required), db=Depends(session)):
    stmt = select(MediaAsset).order_by(MediaAsset.id.desc()).limit(500)
    if kind:
        stmt = stmt.where(MediaAsset.kind == kind)
    return [asset_dict(a) for a in db.scalars(stmt)]


@router.post("/media")
async def upload(file: UploadFile = File(...), kind: str = Form(""), staff=Depends(staff_required), db=Depends(session)):
    data = await file.read(settings().max_upload_bytes + 1)
    asset = media.store(db, data, file.filename or "file", staff.username, kind or None)
    db.commit()
    return asset_dict(asset)


@router.get("/media/{asset_id}/file")
def media_file(asset_id: int, staff=Depends(staff_required), db=Depends(session)):
    asset = require(db, MediaAsset, asset_id)
    path = media.path_of(asset)
    if not path.exists():
        raise HTTPException(404, "not_found")
    return FileResponse(path, media_type=asset.mime, filename=asset.filename)


@router.delete("/media/{asset_id}")
def delete_media(asset_id: int, staff=Depends(staff_required), db=Depends(session)):
    media.remove(db, require(db, MediaAsset, asset_id))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- response templates
def template_dict(t: Template) -> dict:
    return {"id": t.id, "name": t.name, "shortcut": t.shortcut, "texts": t.texts or {}, "department_id": t.department_id,
            "enabled": t.enabled, "position": t.position}


@router.get("/templates")
def list_templates(staff=Depends(staff_required), db=Depends(session)):
    return [template_dict(t) for t in db.scalars(select(Template).order_by(Template.position, Template.name))]


class TemplateData(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    shortcut: str = Field(default="", max_length=32)
    texts: dict[str, str]
    department_id: int | None = None
    enabled: bool = True
    position: int = 0


def _template(t: Template, data: TemplateData, staff) -> None:
    try:
        texts = rich.clean_texts(data.texts, 4096)
    except ValueError as error:
        raise DomainError(rich.reason(error)) from None
    if not texts:
        raise DomainError("empty_text")
    t.name, t.shortcut, t.texts, t.department_id = data.name.strip(), data.shortcut.strip(), texts, data.department_id
    t.enabled, t.position, t.created_by, t.updated_at = data.enabled, data.position, t.created_by or staff.username, now()


@router.post("/templates")
def create_template(data: TemplateData, staff=Depends(staff_required), db=Depends(session)):
    template = Template()
    _template(template, data, staff)
    db.add(template)
    db.commit()
    return template_dict(template)


@router.put("/templates/{template_id}")
def update_template(template_id: int, data: TemplateData, staff=Depends(staff_required), db=Depends(session)):
    template = require(db, Template, template_id)
    _template(template, data, staff)
    db.commit()
    return template_dict(template)


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, staff=Depends(staff_required), db=Depends(session)):
    db.delete(require(db, Template, template_id))
    db.commit()
    return {"ok": True}
