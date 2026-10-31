"""What the bot shows, cached from the backend's content bundle. Dashboard changes bump a version number; the bot
checks it every few seconds and reloads, so changes reach users without a restart."""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from backend.services.faq import Tree
from backend.services.i18n import FALLBACK, Translator, match, pick
from bots.client import ApiClient

log = logging.getLogger("bot")
CHECK_EVERY = 5.0


@dataclass
class Content:
    version: int = -1
    languages: list[dict] = field(default_factory=list)
    default: str = FALLBACK
    t: Translator = field(default_factory=Translator)
    welcome: dict = field(default_factory=dict)
    menu: list[dict] = field(default_factory=list)
    tree: Tree = field(default_factory=lambda: Tree(None))
    departments: list[dict] = field(default_factory=list)
    categories: list[dict] = field(default_factory=list)
    statuses: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)

    @classmethod
    def from_bundle(cls, data: dict) -> "Content":
        rtl = {lang["code"] for lang in data["languages"] if lang.get("rtl")}
        custom = {(lang, key): value for lang, key, value in data.get("texts", [])}
        return cls(version=data["version"], languages=data["languages"], default=data["default"], t=Translator(custom, data["default"], rtl),
                   welcome=data.get("welcome") or {}, menu=data.get("menu") or [], tree=Tree(data.get("faq")),
                   departments=data.get("departments") or [], categories=data.get("categories") or [],
                   statuses=data.get("statuses") or {}, settings=data.get("settings") or {})

    @property
    def codes(self) -> list[str]:
        return [lang["code"] for lang in self.languages]

    def rtl(self, lang: str) -> bool:
        return lang in self.t.rtl

    def language_name(self, code: str) -> str:
        lang = next((x for x in self.languages if x["code"] == code), None)
        return f"{lang.get('flag', '')} {lang['native_name']}".strip() if lang else code

    def name(self, values: dict, lang: str) -> str:
        return pick(values, lang, self.default)

    def categories_of(self, department_id: int | None) -> list[dict]:
        return [c for c in self.categories if c["department_id"] == department_id]

    def resolve(self, code: str | None) -> str:
        return match(code, self.codes) or self.default


class ContentCache:
    def __init__(self, api: ApiClient):
        self.api, self.content, self.checked = api, Content(), 0.0
        self.lock = asyncio.Lock()

    async def get(self) -> Content:
        if self.content.version >= 0 and time.monotonic() - self.checked < CHECK_EVERY:
            return self.content
        async with self.lock:
            if self.content.version >= 0 and time.monotonic() - self.checked < CHECK_EVERY:
                return self.content
            try:
                version = (await self.api.get("version"))["content_version"]
                if version != self.content.version:
                    self.content = Content.from_bundle(await self.api.get("content"))
                    log.info('{"event":"content_loaded","version":%s}', self.content.version)
            except Exception:
                if self.content.version < 0:
                    raise
                log.warning('{"event":"content_refresh_failed"}')  # keep showing the last content
            self.checked = time.monotonic()
        return self.content
