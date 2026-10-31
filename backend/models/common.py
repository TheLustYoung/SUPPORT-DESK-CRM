from datetime import datetime, timezone
from sqlalchemy import JSON, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# Translatable texts are JSON objects {language code: text}: adding a language never changes the schema.
Json = JSON().with_variant(JSONB(), "postgresql")


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; PostgreSQL returns aware ones. Normalize to UTC.
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=timezone.utc)


class UTC(TypeDecorator):
    """Timestamps stored in UTC that always come back timezone-aware (SQLite forgets the zone otherwise)."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        value = aware(value).astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        return aware(value)


def created() -> Mapped[datetime]:
    return mapped_column(UTC, default=now, nullable=False)
