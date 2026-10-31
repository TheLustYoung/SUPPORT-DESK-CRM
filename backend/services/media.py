"""Media library: files uploaded in the dashboard for the welcome message, FAQ answers,
announcements and replies, and copies of users' attachments. Files live on the server disk (STORAGE_DIR, Docker
volume files-data) and are served only to signed-in staff — never through a public URL.

Uploads are checked by their content (magic bytes), not by the name the browser sends. Telegram's file_id is cached
after the first upload, so a photo used in the welcome message is uploaded to Telegram only once.
"""
import hashlib
import mimetypes
import os
import secrets
from pathlib import Path
from sqlalchemy import func, select
from backend.config import settings
from backend.models import Campaign, FaqNode, MediaAsset, MenuItem, Message, Setting
from backend.services.security import DomainError, safe_filename

SIGNATURES = [  # (prefix, offset, kind, mime)
    (b"\xff\xd8\xff", 0, "photo", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", 0, "photo", "image/png"),
    (b"RIFF", 0, "webp", "image/webp"), (b"GIF8", 0, "animation", "image/gif"),
    (b"ftyp", 4, "video", "video/mp4"), (b"\x1aE\xdf\xa3", 0, "video", "video/webm"),
    (b"OggS", 0, "voice", "audio/ogg"), (b"ID3", 0, "audio", "audio/mpeg"), (b"\xff\xfb", 0, "audio", "audio/mpeg"),
    (b"\xff\xf3", 0, "audio", "audio/mpeg"), (b"\xff\xf1", 0, "audio", "audio/aac"), (b"fLaC", 0, "audio", "audio/flac"),
    (b"%PDF-", 0, "document", "application/pdf"), (b"PK\x03\x04", 0, "document", "application/zip"),
]
TEXT_EXTENSIONS = {".txt", ".csv"}
PHOTO_LIMIT = 10_000_000  # Telegram's limit for photos; larger pictures go as documents


def root() -> Path:
    path = Path(settings().storage_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def detect(head: bytes, filename: str, size: int) -> tuple[str, str]:
    """(kind, mime) from the file's first bytes; refuses executables and unknown binaries."""
    for prefix, offset, kind, mime in SIGNATURES:
        if head[offset:offset + len(prefix)] == prefix:
            if kind == "webp":
                kind = "photo" if head[8:12] == b"WEBP" else "document"
                mime = "image/webp" if kind == "photo" else "application/octet-stream"
            if mime == "video/mp4" and head[8:12] in (b"M4A ", b"M4B "):
                kind, mime = "audio", "audio/mp4"
            if kind == "document" and mime == "application/zip":
                mime = mimetypes.guess_type(filename)[0] or mime  # docx, xlsx, pptx are zip files
            if kind == "photo" and size > PHOTO_LIMIT:
                kind = "document"
            return kind, mime
    if Path(filename).suffix.lower() in TEXT_EXTENSIONS:
        try:
            head.decode("utf-8")
            return "document", mimetypes.guess_type(filename)[0] or "text/plain"
        except UnicodeDecodeError:
            pass
    raise DomainError("unsupported_file")


def store(db, data: bytes, filename: str, actor: str, kind: str | None = None) -> MediaAsset:
    """Saves an uploaded file; `kind` may ask for a voice/document/animation instead of the detected type."""
    if not data:
        raise DomainError("empty_file")
    if len(data) > settings().max_upload_bytes:
        raise DomainError("file_too_large", 413)
    detected, mime = detect(data[:64], filename, len(data))
    if kind and kind != detected:
        allowed = {"document": True, "voice": detected in ("audio", "voice"), "audio": detected in ("audio", "voice"),
                   "animation": detected in ("video", "animation"), "video": detected == "animation"}
        if not allowed.get(kind):
            raise DomainError("media_kind")
        detected = kind
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(MediaAsset).where(MediaAsset.sha256 == digest, MediaAsset.kind == detected))
    if existing:
        return existing
    name = safe_filename(filename)
    relative = Path("media") / digest[:2] / f"{secrets.token_hex(8)}{Path(name).suffix.lower()[:10]}"
    target = root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    temporary.write_bytes(data)
    os.replace(temporary, target)
    asset = MediaAsset(kind=detected, filename=name, mime=mime, size=len(data), sha256=digest, path=relative.as_posix(), created_by=actor)
    db.add(asset)
    db.flush()
    return asset


def path_of(asset: MediaAsset) -> Path:
    base = root().resolve()
    target = (base / asset.path).resolve()
    if base not in target.parents:
        raise DomainError("not_found", 404)
    return target


def attachment_path(message_id: int, file_name: str | None) -> Path:
    """Where the copy of a user's attachment is kept."""
    suffix = Path(safe_filename(file_name or "")).suffix.lower()[:10]
    return root() / "attachments" / str(message_id // 1000) / f"{message_id}{suffix}"


def usage(db, asset_id: int) -> int:
    """How many places use a file (it cannot be deleted while used)."""
    used = db.scalar(select(func.count()).select_from(Message).where(Message.asset_id == asset_id)) or 0
    needle = f"{asset_id}"
    for row in [*db.scalars(select(FaqNode.content)), *db.scalars(select(MenuItem.content)), *db.scalars(select(Campaign.content))]:
        used += sum(1 for b in (row or {}).get("blocks", []) if int(needle) in (b.get("assets") or []))
    welcome = db.get(Setting, "welcome")
    if welcome:
        used += sum(1 for b in (welcome.value or {}).get("blocks", []) if int(needle) in (b.get("assets") or []))
    return used


def remove(db, asset: MediaAsset) -> None:
    if usage(db, asset.id):
        raise DomainError("media_in_use")
    try:
        path_of(asset).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(asset)
