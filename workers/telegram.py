"""Minimal synchronous Telegram Bot API client used by the workers to deliver messages."""
import json
from pathlib import Path
import httpx
from backend.config import settings


class TelegramError(Exception):
    """Telegram refused the request for good; `description` is Telegram's own text (never contains the token)."""

    def __init__(self, description: str, status: int = 400):
        super().__init__(description)
        self.description, self.status = description, status

    @property
    def blocked(self) -> bool:
        """The user blocked the bot, deleted their account or never started it: stop writing to them."""
        text = self.description.lower()
        return self.status == 403 or "chat not found" in text or "user is deactivated" in text


class TelegramRetry(Exception):
    """Temporary failure or flood control; `retry_after` seconds when Telegram says so."""

    def __init__(self, retry_after: int | None = None):
        super().__init__("Telegram temporarily unavailable")
        self.retry_after = retry_after


class Telegram:
    def __init__(self, token: str | None = None):
        self.base = f"https://api.telegram.org/bot{token or settings().bot_token.get_secret_value()}/"
        self.client = httpx.Client(timeout=60, follow_redirects=False)

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def call(self, method: str, data: dict, files=None):
        # Never log the URL: Telegram embeds the bot token in it.
        body = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in data.items() if v is not None}
        try:
            response = self.client.post(self.base + method, data=body, files=files)
        except httpx.HTTPError:
            raise TelegramRetry() from None
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code >= 500 or response.status_code == 429:
            raise TelegramRetry((payload.get("parameters") or {}).get("retry_after"))
        if response.status_code != 200 or not payload.get("ok"):
            raise TelegramError(str(payload.get("description") or f"HTTP {response.status_code}")[:300], response.status_code)
        return payload["result"]

    def execute(self, chat_id: int, calls: list[dict], asset_source) -> list[dict]:
        """Runs a render plan (backend.services.render). `asset_source(asset_id)` returns (path, cached file_id, remember)."""
        results = []
        for call in calls:
            data = {"chat_id": chat_id, **{k: v for k, v in call["data"].items() if v is not None}}
            if call["method"] == "sendMediaGroup":
                results.append(self._album(chat_id, call["media"], asset_source))
                continue
            if call.get("asset"):
                path, file_id, remember = asset_source(call["asset"])
                field = call["field"]
                if file_id:
                    results.append(self.call(call["method"], {**data, field: file_id}))
                    continue
                with Path(path).open("rb") as body:
                    result = self.call(call["method"], data, files={field: (Path(path).name, body, "application/octet-stream")})
                remember(sent_file_id(result, field))
                results.append(result)
                continue
            results.append(self.call(call["method"], data))
        return results

    def _album(self, chat_id: int, items: list[dict], asset_source):
        media, files, handles, remembers = [], {}, [], []
        try:
            for index, item in enumerate(items):
                path, file_id, remember = asset_source(item["asset"])
                kind = "video" if str(path).lower().endswith((".mp4", ".mov", ".webm")) else "photo"
                entry = {"type": kind, "media": file_id or f"attach://f{index}"}
                if item.get("caption"):
                    entry.update(caption=item["caption"], parse_mode="HTML")
                if not file_id:
                    handle = Path(path).open("rb")
                    handles.append(handle)
                    files[f"f{index}"] = (Path(path).name, handle, "application/octet-stream")
                    remembers.append((index, kind, remember))
                media.append(entry)
            result = self.call("sendMediaGroup", {"chat_id": chat_id, "media": media}, files=files or None)
        finally:
            for handle in handles:
                handle.close()
        for index, kind, remember in remembers:
            if index < len(result):
                remember(sent_file_id(result[index], kind))
        return result

    def file_info(self, file_id: str) -> dict:
        return self.call("getFile", {"file_id": file_id})

    def download(self, remote_path: str, destination: Path, max_size: int) -> int:
        if ".." in remote_path or remote_path.startswith("/"):
            raise TelegramError("bad file path")
        url = self.base.replace("/bot", "/file/bot", 1) + remote_path
        size = 0
        temporary = destination.with_suffix(destination.suffix + ".part")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.client.stream("GET", url) as response:
            if response.status_code != 200:
                raise TelegramRetry()
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes(256 * 1024):
                    size += len(chunk)
                    if size > max_size:
                        raise TelegramError("file too large")
                    output.write(chunk)
        temporary.replace(destination)
        return size


def sent_file_id(result: dict, field: str) -> str | None:
    """Telegram's file_id of what was just uploaded (photos come in several sizes: the largest one)."""
    if not isinstance(result, dict):
        return None
    if field == "photo" and result.get("photo"):
        return result["photo"][-1]["file_id"]
    for key in (field, "document", "video", "audio", "voice", "animation"):
        if isinstance(result.get(key), dict):
            return result[key].get("file_id")
    return None
