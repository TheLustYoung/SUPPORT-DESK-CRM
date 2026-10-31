"""Translations with a documented fallback:

1. the text in the user's language (as edited in the dashboard, else the built-in one);
2. the same text in the default language;
3. English;
4. any language that has it (translatable content only).

Built-in texts live in backend/services/locales/<code>.json; a language without a file still works (it falls back),
and its texts can be written in the dashboard. Translation keys are never shown to users: a missing key falls back
to English, and an English text exists for every key (checked by the tests). Values inserted into texts are
HTML-escaped; right-to-left languages get a direction mark so Telegram aligns them to the right.
"""
import html
import json
from functools import lru_cache
from pathlib import Path

LOCALES = Path(__file__).resolve().parent / "locales"
FALLBACK = "en"
RLM = "‏"
LEGACY = {"iw": "he", "in": "id", "ji": "yi", "zh-hans": "zh", "zh-cn": "zh", "zh-tw": "zh", "pt-pt": "pt"}

# Languages that can be added in one click (Settings → Languages); any other IETF code can be typed in.
KNOWN = {
    "en": ("English", "English", "🇬🇧", False), "ru": ("Russian", "Русский", "🇷🇺", False),
    "ar": ("Arabic", "العربية", "🇸🇦", True), "he": ("Hebrew", "עברית", "🇮🇱", True),
    "es": ("Spanish", "Español", "🇪🇸", False), "fr": ("French", "Français", "🇫🇷", False),
    "de": ("German", "Deutsch", "🇩🇪", False), "pt": ("Portuguese", "Português", "🇵🇹", False),
    "it": ("Italian", "Italiano", "🇮🇹", False), "tr": ("Turkish", "Türkçe", "🇹🇷", False),
    "uk": ("Ukrainian", "Українська", "🇺🇦", False), "pl": ("Polish", "Polski", "🇵🇱", False),
    "zh": ("Chinese", "中文", "🇨🇳", False), "hi": ("Hindi", "हिन्दी", "🇮🇳", False),
    "bn": ("Bengali", "বাংলা", "🇧🇩", False), "ur": ("Urdu", "اردو", "🇵🇰", True),
    "fa": ("Persian", "فارسی", "🇮🇷", True), "id": ("Indonesian", "Bahasa Indonesia", "🇮🇩", False),
    "ja": ("Japanese", "日本語", "🇯🇵", False), "ko": ("Korean", "한국어", "🇰🇷", False),
    "kk": ("Kazakh", "Қазақша", "🇰🇿", False), "uz": ("Uzbek", "Oʻzbekcha", "🇺🇿", False),
    "az": ("Azerbaijani", "Azərbaycanca", "🇦🇿", False), "hy": ("Armenian", "Հայերեն", "🇦🇲", False),
    "ka": ("Georgian", "ქართული", "🇬🇪", False), "vi": ("Vietnamese", "Tiếng Việt", "🇻🇳", False),
    "th": ("Thai", "ไทย", "🇹🇭", False), "nl": ("Dutch", "Nederlands", "🇳🇱", False),
}


@lru_cache
def builtin() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(LOCALES.glob("*.json"))}


def builtin_keys() -> list[str]:
    return [k for k in builtin()[FALLBACK] if not k.startswith("_")]


def normalize(code: str | None) -> str:
    code = (code or "").strip().lower().replace("_", "-")
    return LEGACY.get(code, code)


def match(code: str | None, available) -> str | None:
    """The enabled language for a Telegram language code: exact (pt-br), then the base language (pt)."""
    code = normalize(code)
    if not code:
        return None
    if code in available:
        return code
    base = LEGACY.get(code.split("-")[0], code.split("-")[0])
    return base if base in available else None


def chain(lang: str | None, default: str) -> list[str]:
    order = []
    for code in (lang, default, FALLBACK):
        if code and code not in order:
            order.append(code)
    return order


def pick(values: dict | None, lang: str | None, default: str = FALLBACK) -> str:
    """One translation out of {language: text}, following the fallback chain."""
    if not values:
        return ""
    for code in chain(lang, default):
        if values.get(code):
            return values[code]
    return next((v for v in values.values() if v), "")


def escape(value) -> str:
    return html.escape(str(value), quote=False)


class Translator:
    """Texts for one moment: the dashboard's edits on top of the built-in catalog."""

    def __init__(self, custom: dict[tuple[str, str], str] | None = None, default: str = FALLBACK, rtl: set[str] | None = None):
        self.custom, self.default, self.rtl = custom or {}, default, rtl or set()

    def raw(self, lang: str | None, key: str) -> str:
        for code in chain(lang, self.default):
            value = self.custom.get((code, key)) or builtin().get(code, {}).get(key)
            if value:
                return value
        return ""  # never the key itself: users must not see translation keys

    def __call__(self, lang: str | None, key: str, **values) -> str:
        safe = {k: escape(v) for k, v in values.items()}
        text = self.raw(lang, key)
        try:
            text = text.format(**safe)
        except (KeyError, IndexError, ValueError):  # an edited text with a broken placeholder: the built-in one
            fallback = next((builtin().get(c, {}).get(key) for c in chain(lang, self.default) if builtin().get(c, {}).get(key)), "")
            text = fallback.format(**safe) if fallback else ""
        return self.direction(lang, text)

    def direction(self, lang: str | None, text: str) -> str:
        return RLM + text if lang in self.rtl and text and not text.startswith(RLM) else text

    def label(self, lang: str | None, key: str) -> str:
        """Button text: no direction mark (Telegram centres buttons)."""
        return self.raw(lang, key)
