"""Error tracking: with SENTRY_DSN set in .env, unhandled errors of the API, the workers and the bot are
reported to Sentry (or a self-hosted, Sentry-compatible service such as GlitchTip). Without it nothing is sent.

Reports never include personal data or secrets: request bodies, cookies and user details are not attached
(send_default_pii=False), and bot tokens are removed from any text before sending.
"""
import logging
import re
from backend.config import settings

TOKEN = re.compile(r"\d{5,15}:[A-Za-z0-9_-]{30,64}")
_started = False


class JsonFormatter(logging.Formatter):
    """One JSON object per line, tokens removed."""

    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        import json
        entry = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname, "service": self.service,
                 "logger": record.name, "message": scrub(record.getMessage())}
        if record.exc_info:
            entry["error"] = scrub(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(service: str, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def scrub(value):
    """Removes bot tokens from messages and exception values."""
    if isinstance(value, str):
        return TOKEN.sub("[token]", value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def setup(service: str) -> bool:
    global _started
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines contain Telegram URLs with the token
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    dsn = settings().sentry_dsn.get_secret_value()
    if not dsn or _started:
        return _started
    import sentry_sdk
    sentry_sdk.init(dsn=dsn, environment=settings().environment, server_name=service, send_default_pii=False,
                    traces_sample_rate=0.0, before_send=lambda event, _hint: scrub(event))
    sentry_sdk.set_tag("service", service)
    _started = True
    return True
