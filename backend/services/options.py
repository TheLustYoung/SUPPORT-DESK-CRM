"""General settings changed in the dashboard (Settings → General), with their defaults; business hours."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy import select
from backend.models import Setting, now

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULTS: dict[str, object] = {
    "company_name": "Support",
    "ticket_prefix": "T-",
    "ask_department": True,  # «Contact support» asks for the department when more than one is enabled
    "ask_category": True,  # …and for the topic when the department has categories
    "reopen_days": 7,  # a closed ticket can be reopened by its user for this many days
    "auto_close_resolved_hours": 72,  # resolved tickets close by themselves (0 = never)
    "csat_enabled": True,  # ask for a 1–5 rating when a ticket is resolved
    "search_enabled": True,  # free text without a ticket searches the FAQ
    "ask_language_first": False,  # show the language choice on the very first /start
    "messages_per_minute": 20,  # per user; protects the team from floods
    "max_attachment_mb": 19,  # Telegram lets bots download files up to 20 MB
    "faq_max_depth": 6,
    "faq_max_children": 40,
    "faq_max_nodes": 1000,
    "campaign_rate_per_second": 20,  # Telegram allows about 30 messages per second for a bot
    "business_hours": {"enabled": False, "timezone": "Asia/Jerusalem",
                       "days": {d: (["09:00", "18:00"] if d not in ("sat", "sun") else None) for d in DAYS}},
    "offline_auto_reply": True,  # tell users when the team is away (outside business hours)
    "privacy_notice": {},  # {lang: text} shown with the first /start: what is stored and why; empty = none
    "privacy_url": "",  # link to the privacy policy, shown with the notice
    "sla_business_hours": False,  # SLA targets count only business hours (nights and weekends do not count)
    "sla_pause_pending_user": False,  # SLA time stops while a ticket waits for the user's reply
    # scheduled jobs
    "remind_user_hours": 24,  # remind the user once when a ticket waits for their answer this long (0 = never)
    "sla_warning_percent": 80,  # warn the agent when this share of an SLA target has passed (0 = never)
    "weekly_report": True,  # Monday 09:00 (business-hours time zone): last week's summary to staff with analytics.view
    "retention_events_days": 365,  # analytics events older than this are removed (0 = keep)
    "retention_inactive_users_days": 0,  # anonymize users inactive this long (0 = never)
    "retention_closed_tickets_days": 0,  # delete attachments copies of tickets closed this long ago (0 = keep)
    "retention_logs_days": 90,  # error log and staff notifications older than this are removed (0 = keep); audit log: kept
}
LIMITS = {"reopen_days": (0, 365), "auto_close_resolved_hours": (0, 24 * 90), "messages_per_minute": (1, 120),
          "max_attachment_mb": (1, 19), "faq_max_depth": (1, 10), "faq_max_children": (1, 100), "faq_max_nodes": (10, 5000),
          "campaign_rate_per_second": (1, 30), "retention_events_days": (0, 3650), "retention_inactive_users_days": (0, 3650),
          "retention_closed_tickets_days": (0, 3650), "remind_user_hours": (0, 720), "sla_warning_percent": (0, 95),
          "retention_logs_days": (0, 3650)}
INTERNAL = {"seeded", "content_version", "welcome", "round_robin", "weekly_report_week", "scheduler_seen"}
SCHEDULER_STALE = 180  # seconds: the every-minute job missed three runs — the scheduler or the workers are down


def scheduler_alive(db) -> tuple[bool, str | None]:
    """(alive, last run): the every-minute job proves both the scheduler (beat) and a worker are running."""
    seen = get(db, "scheduler_seen")
    if not seen:
        return False, None
    try:
        moment = datetime.fromisoformat(seen)
    except ValueError:
        return False, None
    return (now() - moment).total_seconds() < SCHEDULER_STALE, seen  # not editable on Settings → General


def get(db, key: str):
    row = db.get(Setting, key)
    return row.value if row is not None else DEFAULTS.get(key)


def all_settings(db) -> dict:
    stored = {row.key: row.value for row in db.scalars(select(Setting))}
    return {key: stored.get(key, default) for key, default in DEFAULTS.items()}


def put(db, key: str, value) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, updated_at=now()))
    else:
        row.value, row.updated_at = value, now()


def validate(changes: dict) -> dict:
    """Checks a Settings → General update; raises ValueError with the offending key."""
    clean = {}
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise ValueError(key)
        default = DEFAULTS[key]
        if key == "business_hours":
            if not valid_hours(value):
                raise ValueError(key)
            clean[key] = {"enabled": bool(value.get("enabled")), "timezone": value["timezone"],
                          "days": {d: (list(value["days"][d]) if value["days"].get(d) else None) for d in DAYS}}
        elif key == "privacy_notice":
            if not isinstance(value, dict):
                raise ValueError(key)
            from backend.services import rich
            try:
                clean[key] = rich.clean_texts(value, 1000)
            except ValueError:
                raise ValueError(key) from None
        elif key == "privacy_url":
            text = str(value or "").strip()
            if text and not text.startswith(("https://", "http://")):
                raise ValueError(key)
            clean[key] = text[:500]
        elif isinstance(default, bool):
            clean[key] = bool(value)
        elif isinstance(default, int):
            low, high = LIMITS.get(key, (0, 10 ** 6))
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ValueError(key)
            clean[key] = value
        else:
            text = str(value or "").strip()
            if not text or len(text) > (12 if key == "ticket_prefix" else 120):
                raise ValueError(key)
            clean[key] = text
    return clean


def bump_content_version(db) -> None:
    """Tells the running bot to reload its cached content (menu, FAQ, texts, languages)."""
    put(db, "content_version", int(get(db, "content_version") or 0) + 1)


def _clock(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def valid_hours(value) -> bool:
    try:
        ZoneInfo(value["timezone"])
        for day in DAYS:
            span = value["days"].get(day)
            if span is not None and _clock(span[0]) == _clock(span[1]):
                return False
        return True
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError, IndexError, AttributeError):
        return False


def is_open(hours: dict, at: datetime | None = None) -> bool:
    """Within business hours (always open when business hours are off). Spans may pass midnight (22:00–06:00)."""
    if not hours or not hours.get("enabled"):
        return True
    try:
        local = (at or datetime.now(timezone.utc)).astimezone(ZoneInfo(hours.get("timezone") or "UTC"))
    except ZoneInfoNotFoundError:
        return True
    today, yesterday = DAYS[local.weekday()], DAYS[(local.weekday() - 1) % 7]
    moment = local.time()
    span = (hours.get("days") or {}).get(today)
    if span:
        start, end = _clock(span[0]), _clock(span[1])
        if (start < end and start <= moment < end) or (start > end and moment >= start):
            return True
    previous = (hours.get("days") or {}).get(yesterday)
    if previous:
        start, end = _clock(previous[0]), _clock(previous[1])
        if start > end and moment < end:
            return True
    return False


def open_spans(hours: dict, start: datetime, days: int = 400):
    """Business-hour spans (UTC start, UTC end) from the local day before `start` on, in time order."""
    zone = ZoneInfo(hours.get("timezone") or "UTC")
    first = start.astimezone(zone).date() - timedelta(days=1)
    for offset in range(days):
        day = first + timedelta(days=offset)
        span = (hours.get("days") or {}).get(DAYS[day.weekday()])
        if not span:
            continue
        begin, finish = _clock(span[0]), _clock(span[1])
        opened = datetime.combine(day, begin, tzinfo=zone)
        closed = datetime.combine(day + timedelta(days=1) if finish <= begin else day, finish, tzinfo=zone)
        yield opened.astimezone(timezone.utc), closed.astimezone(timezone.utc)


def has_hours(hours: dict) -> bool:
    return bool(hours and hours.get("enabled") and any((hours.get("days") or {}).get(d) for d in DAYS))


def add_working_seconds(start: datetime, seconds: float, hours: dict) -> datetime:
    """The moment `seconds` of business time after `start` (calendar time when business hours are off)."""
    if not has_hours(hours):
        return start + timedelta(seconds=seconds)
    left = seconds
    for opened, closed in open_spans(hours, start):
        if closed <= start:
            continue
        begin = max(opened, start)
        available = (closed - begin).total_seconds()
        if left <= available:
            return begin + timedelta(seconds=left)
        left -= available
    return start + timedelta(seconds=seconds)  # no business hours within a year: calendar time


def working_seconds(start: datetime, end: datetime, hours: dict) -> float:
    """Business time between two moments (calendar time when business hours are off)."""
    if end <= start:
        return 0.0
    if not has_hours(hours):
        return (end - start).total_seconds()
    total = 0.0
    for opened, closed in open_spans(hours, start, days=(end - start).days + 3):
        if opened >= end:
            break
        begin, finish = max(opened, start), min(closed, end)
        if finish > begin:
            total += (finish - begin).total_seconds()
    return total


def sla_hours(db) -> dict:
    """The business hours SLA counts in, or {} for calendar time (Settings → Business hours and SLA)."""
    return get(db, "business_hours") if get(db, "sla_business_hours") else {}


def describe_hours(hours: dict) -> str:
    """Short text for users, e.g. «Mon–Fri 09:00–18:00»."""
    days = (hours or {}).get("days") or {}
    groups: list[tuple[list[str], tuple]] = []
    for day in DAYS:
        span = tuple(days.get(day) or ())
        if not span:
            continue
        if groups and groups[-1][1] == span and DAYS.index(groups[-1][0][-1]) == DAYS.index(day) - 1:
            groups[-1][0].append(day)
        else:
            groups.append(([day], span))
    return ", ".join(f"{g[0].capitalize()}{'–' + g[-1].capitalize() if len(g) > 1 else ''} {s[0]}–{s[1]}" for g, s in groups) or "—"
