"""Rich content: what the welcome message, FAQ answers, menu pages and announcements are made of.

    {"blocks": [{"type": "text" | "photo" | "album" | "video" | "audio" | "voice" | "document" | "animation",
                 "text": {"en": "...", "ru": "..."}, "assets": [media asset ids], "enabled": true}],
     "buttons": [{"label": {"en": "..."}, "action": "url" | "node" | "faq" | "support" | "tickets" | "menu" | "language",
                  "value": "https://… or a FAQ question id"}]}

Every block's text and every button label is translated separately; media are shared by all languages.
Texts may use Telegram's HTML tags (<b>, <i>, <a href>, …); they are checked before saving. Telegram limits are
checked too: 4096 characters of text, 1024 of a caption, 2–10 items in an album. Telegram cannot attach buttons to
an album, so the bot sends the buttons with the last single message (or a short text after the album).
"""
import re
from html.parser import HTMLParser
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

BlockType = Literal["text", "photo", "album", "video", "audio", "voice", "document", "animation"]
Action = Literal["url", "node", "faq", "support", "tickets", "menu", "language"]
MEDIA_KINDS = {"photo": {"photo"}, "album": {"photo", "video"}, "video": {"video"}, "audio": {"audio"}, "voice": {"voice", "audio"},
               "document": {"document", "photo", "video", "audio", "voice", "animation"}, "animation": {"animation", "video"},
               "text": set()}
TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre", "tg-spoiler", "blockquote", "br"}
TEXT_LIMIT, CAPTION_LIMIT = 4096, 1024


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.bad = [], False

    def handle_starttag(self, tag, attrs):
        if tag == "span" and dict(attrs).get("class") == "tg-spoiler":
            tag = "tg-spoiler"
        if tag not in TAGS:
            self.bad = True
        if tag == "a" and not str(dict(attrs).get("href") or "").startswith(("https://", "http://", "tg://")):
            self.bad = True
        if tag != "br":
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "br":
            return
        if tag == "span":
            tag = "tg-spoiler"
        if not self.stack or self.stack.pop() != tag:
            self.bad = True


def check_html(text: str) -> bool:
    parser = _Tags()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return False
    return not parser.bad and not parser.stack


def to_telegram(text: str) -> str:
    """<br> is not a Telegram tag: new lines instead."""
    return re.sub(r"<br\s*/?>", "\n", text or "")


def clean_texts(values: dict, limit: int) -> dict:
    result = {}
    for code, text in (values or {}).items():
        text = str(text or "").strip()
        if not text:
            continue
        if len(text) > limit:
            raise ValueError(f"text_too_long:{code}")
        if not check_html(text):
            raise ValueError(f"bad_html:{code}")
        result[str(code)[:12]] = text
    return result


class Button(BaseModel):
    label: dict[str, str] = Field(default_factory=dict)
    action: Action = "url"
    value: str = ""

    @field_validator("label")
    @classmethod
    def labels(cls, value):
        clean = {k[:12]: str(v).strip()[:64] for k, v in (value or {}).items() if str(v or "").strip()}
        if not clean:
            raise ValueError("button_label")
        return clean

    @model_validator(mode="after")
    def target(self):
        if self.action == "url" and not self.value.startswith(("https://", "http://", "tg://")):
            raise ValueError("button_url")
        if self.action == "node" and not self.value.isdigit():
            raise ValueError("button_node")
        return self


class Block(BaseModel):
    type: BlockType = "text"
    text: dict[str, str] = Field(default_factory=dict)
    assets: list[int] = Field(default_factory=list, max_length=10)
    enabled: bool = True

    @model_validator(mode="after")
    def shape(self):
        self.text = clean_texts(self.text, TEXT_LIMIT if self.type == "text" else CAPTION_LIMIT)
        if self.type == "text":
            self.assets = []
            if not self.text:
                raise ValueError("empty_text")
        elif self.type == "album":
            if not 2 <= len(self.assets) <= 10:
                raise ValueError("album_size")
        elif len(self.assets) != 1:
            raise ValueError("media_missing")
        return self


class Rich(BaseModel):
    blocks: list[Block] = Field(default_factory=list, max_length=10)
    buttons: list[Button] = Field(default_factory=list, max_length=20)

    def asset_ids(self) -> set[int]:
        return {a for b in self.blocks for a in b.assets}

    def languages(self) -> set[str]:
        return {code for b in self.blocks for code in b.text}

    @property
    def empty(self) -> bool:
        return not any(b.enabled for b in self.blocks)


ERRORS = ("text_too_long", "bad_html", "album_size", "media_missing", "empty_text", "button_label", "button_url", "button_node",
          "media_kind")


def parse(value: dict | None) -> Rich:
    return Rich.model_validate(value or {})


def reason(error: Exception) -> str:
    """The error code of a refused content (shown translated in the dashboard)."""
    text = str(error)
    return next((code for code in ERRORS if code in text), "bad_content")


def check_assets(db, content: Rich) -> None:
    """Every referenced media file exists and fits its block (a photo block holds a photo, …)."""
    from backend.models import MediaAsset
    for block in content.blocks:
        for asset_id in block.assets:
            asset = db.get(MediaAsset, asset_id)
            if asset is None:
                raise ValueError("media_missing")
            if asset.kind not in MEDIA_KINDS[block.type]:
                raise ValueError("media_kind")


def plain(text: str) -> str:
    """Rich text without tags (search, previews)."""
    return re.sub(r"<[^>]+>", "", text or "")
