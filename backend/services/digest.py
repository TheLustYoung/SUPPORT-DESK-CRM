"""Scheduled reporting: every Monday at 09:00 in the business-hours time zone, staff with
`analytics.view` get last week's summary — in the dashboard bell and, if linked, in Telegram, each in their language.
The numbers are the ticket report of the seven days before (rules: docs/ANALYTICS.md)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from backend.models import Staff, now
from backend.services import analytics, notify, options

SEND_AT = (0, 9)  # Monday, 09:00 local time


def _zone(db) -> ZoneInfo:
    try:
        return ZoneInfo((options.get(db, "business_hours") or {}).get("timezone") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def weekly(db, moment: datetime | None = None, force: bool = False) -> bool:
    """Sends the summary once per ISO week, from Monday 09:00 on; True when it was sent now."""
    if not options.get(db, "weekly_report") and not force:
        return False
    local = (moment or now()).astimezone(_zone(db))
    week = f"{local.isocalendar().year}-W{local.isocalendar().week:02d}"
    # Monday from 09:00; on Tuesday it catches up when the server was down on Monday. Never twice in one week.
    due = (local.weekday() == SEND_AT[0] and local.hour >= SEND_AT[1]) or local.weekday() == SEND_AT[0] + 1
    if not force and (not due or options.get(db, "weekly_report_week") == week):
        return False
    monday = local.date() - timedelta(days=local.weekday())
    first, last = monday - timedelta(days=7), monday - timedelta(days=1)  # always the previous Monday–Sunday
    report = analytics.ticket_report(db, {"date_from": first.isoformat(), "date_to": last.isoformat()})
    params = {"period": f"{first:%d.%m}–{last:%d.%m}", "created": report["total"], "resolved": report["resolved"],
              "open": report["open"], "overdue": report["overdue"],
              "sla": "—" if report["sla_compliance"] is None else f"{report['sla_compliance']}%",
              "csat": "—" if report["csat"] is None else report["csat"]}
    readers = [s for s in db.query(Staff).filter(Staff.active.is_(True)).all() if "analytics.view" in s.permissions]
    notify.send(db, readers, "report", None, params)
    options.put(db, "weekly_report_week", week)
    return True
