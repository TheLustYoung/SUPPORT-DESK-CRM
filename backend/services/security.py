import re


class DomainError(ValueError):
    """Public error code (a translation key in the bot and the dashboard); never contains internal details."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def safe_filename(name: str) -> str:
    """Keep Unicode letters (Arabic, Cyrillic, ...) but strip paths and control characters."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.() -]", "_", name, flags=re.UNICODE).strip(" .")
    return name[:180] or "file.bin"
