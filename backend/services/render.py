"""Rich content → the Telegram Bot API calls that show it, for one language.

The bot (interactive answers) and the workers (replies, notifications, announcements, previews) both send content
this way, so a welcome message, a FAQ answer and an announcement look the same wherever they come from.

A plan is a list of calls: {"method": "sendPhoto", "data": {...}, "asset": 12 | None, "field": "photo"}. When an
item has an asset, the executor sends Telegram's cached file_id if it has one, else uploads the file and remembers the
returned file_id. Telegram cannot attach buttons to an album, so the buttons go with the last single message, or with
a short text after a final album.
"""
from backend.services import rich
from backend.services.i18n import RLM, pick

METHODS = {"photo": "sendPhoto", "video": "sendVideo", "audio": "sendAudio", "voice": "sendVoice", "document": "sendDocument",
           "animation": "sendAnimation"}


def callback(action: str, value: str = "") -> str:
    """Callback_data of a content button (the bot routes it): at most 64 bytes."""
    return {"node": f"faq:{value}", "faq": "faq:root", "support": "support", "tickets": "tickets", "menu": "menu",
            "language": "lang"}.get(action, "menu")


def keyboard(buttons: list[dict], lang: str, default: str, extra: list[list[dict]] | None = None) -> dict | None:
    rows = []
    for button in buttons or []:
        label = pick(button.get("label"), lang, default)
        if not label:
            continue
        if button.get("action") == "url":
            rows.append([{"text": label, "url": button.get("value", "")}])
        else:
            rows.append([{"text": label, "callback_data": callback(button.get("action", ""), str(button.get("value", "")))}])
    rows += extra or []
    return {"inline_keyboard": rows} if rows else None


def _text(values: dict, lang: str, default: str, rtl: bool) -> str:
    text = rich.to_telegram(pick(values, lang, default))
    return RLM + text if rtl and text and not text.startswith(RLM) else text


def plan(content: dict | None, lang: str, default: str, *, rtl: bool = False, extra_buttons: list[list[dict]] | None = None,
         empty_text: str = "", buttons_text: str = "⬇️") -> list[dict]:
    """The calls that show `content` in `lang`; `extra_buttons` (Back, Main menu…) come after the content's own buttons."""
    content = content or {}
    blocks = [b for b in content.get("blocks", []) if b.get("enabled", True)]
    markup = keyboard(content.get("buttons", []), lang, default, extra_buttons)
    calls: list[dict] = []
    for block in blocks:
        kind, text = block.get("type", "text"), _text(block.get("text") or {}, lang, default, rtl)
        if kind == "text":
            if text:
                calls.append({"method": "sendMessage", "data": {"text": text[:4096], "parse_mode": "HTML",
                                                                 "link_preview_options": {"is_disabled": True}}})
        elif kind == "album":
            media = [{"asset": asset_id, "caption": text[:1024] if index == 0 and text else None} for index, asset_id in enumerate(block.get("assets", []))]
            calls.append({"method": "sendMediaGroup", "data": {}, "media": media})
        else:
            assets = block.get("assets") or []
            if not assets:
                continue
            data = {"caption": text[:1024] or None, "parse_mode": "HTML" if text else None}
            calls.append({"method": METHODS.get(kind, "sendDocument"), "data": data, "asset": assets[0], "field": kind if kind in METHODS else "document"})
    if not calls and empty_text:
        calls.append({"method": "sendMessage", "data": {"text": empty_text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}})
    if markup:
        if calls and calls[-1]["method"] != "sendMediaGroup":
            calls[-1]["data"]["reply_markup"] = markup
        else:
            calls.append({"method": "sendMessage", "data": {"text": buttons_text, "reply_markup": markup}})
    return calls


def message_plan(kind: str, text: str | None, asset_id: int | None, markup: dict | None = None, file_id: str | None = None) -> list[dict]:
    """One outgoing message: a text, or a file with its caption — a dashboard upload (asset) or a file an agent sent to
    the bot (Telegram's file_id, reusable by the same bot without uploading)."""
    if asset_id or file_id:
        field = kind if kind in METHODS else "document"
        data = {"caption": (text or "")[:1024] or None, "parse_mode": "HTML" if text else None}
        if markup:
            data["reply_markup"] = markup
        if asset_id:
            return [{"method": METHODS.get(field, "sendDocument"), "data": data, "asset": asset_id, "field": field}]
        return [{"method": METHODS.get(field, "sendDocument"), "data": {**data, field: file_id}}]
    data = {"text": (text or "")[:4096], "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    if markup:
        data["reply_markup"] = markup
    return [{"method": "sendMessage", "data": data}]
