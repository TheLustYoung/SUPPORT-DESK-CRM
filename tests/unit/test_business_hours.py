"""SLA in business hours: nights and weekends do not count."""
from datetime import datetime, timezone
from backend.services import options

WEEK = {"enabled": True, "timezone": "Europe/Moscow",  # UTC+3
        "days": {"mon": ["09:00", "18:00"], "tue": ["09:00", "18:00"], "wed": ["09:00", "18:00"], "thu": ["09:00", "18:00"],
                 "fri": ["09:00", "18:00"], "sat": None, "sun": None}}


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_friday_evening_ticket_is_due_on_monday():
    friday_17_msk = utc(2026, 10, 23, 14, 0)  # one business hour left on Friday
    due = options.add_working_seconds(friday_17_msk, 4 * 3600, WEEK)
    assert due == utc(2026, 10, 26, 9, 0)  # Monday 12:00 Moscow: 1 h on Friday + 3 h on Monday


def test_ticket_at_night_starts_counting_in_the_morning():
    tuesday_3_msk = utc(2026, 10, 27, 0, 0)
    assert options.add_working_seconds(tuesday_3_msk, 30 * 60, WEEK) == utc(2026, 10, 27, 6, 30)  # 09:30 Moscow


def test_calendar_time_when_business_hours_are_off():
    start = utc(2026, 10, 23, 14, 0)
    assert options.add_working_seconds(start, 3600, {**WEEK, "enabled": False}) == utc(2026, 10, 23, 15, 0)
    assert options.add_working_seconds(start, 3600, {}) == utc(2026, 10, 23, 15, 0)


def test_working_seconds_skip_the_weekend():
    friday_17_msk, monday_10_msk = utc(2026, 10, 23, 14, 0), utc(2026, 10, 26, 7, 0)
    assert options.working_seconds(friday_17_msk, monday_10_msk, WEEK) == 2 * 3600


def test_overnight_shift():
    night = {"enabled": True, "timezone": "UTC", "days": {d: ["22:00", "06:00"] for d in options.DAYS}}
    assert options.add_working_seconds(utc(2026, 10, 26, 12, 0), 3 * 3600, night) == utc(2026, 10, 27, 1, 0)
