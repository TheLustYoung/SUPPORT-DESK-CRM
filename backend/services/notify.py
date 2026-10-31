"""Notifications for staff: new tickets, replies, assignments, escalations and overdue tickets.

Every notification appears in the dashboard (the bell, with a sound). Staff who linked their Telegram account
(Profile → Telegram notifications) also get it from the bot.
"""
from sqlalchemy import select
from backend.models import Staff, StaffDepartment, StaffNotification, Ticket
from backend.services import queue


def active_staff(db) -> list[Staff]:
    return list(db.scalars(select(Staff).where(Staff.active.is_(True))))


def department_members(db, department_id: int | None) -> set[int]:
    if department_id is None:
        return set()
    return set(db.scalars(select(StaffDepartment.staff_id).where(StaffDepartment.department_id == department_id)))


def audience(db, ticket: Ticket, *, supervisors: bool = False) -> list[Staff]:
    """Who should hear about a ticket: its agent; else its department; supervisors when asked (escalations, overdue)."""
    members = department_members(db, ticket.department_id)
    chosen = []
    for person in active_staff(db):
        permissions = person.permissions
        if ticket.assignee_id == person.id:
            chosen.append(person)
        elif supervisors and "tickets.assign" in permissions:
            chosen.append(person)
        elif ticket.assignee_id is None and "tickets.view" in permissions and (
                person.id in members or "tickets.view_all" in permissions or (not members and ticket.department_id is None)):
            chosen.append(person)
    return chosen


# kind → bot text (backend/services/locales); the dashboard has the same kinds as notif_<kind>. A notification stores its kind and values, and each person reads it in their own language.
KEYS = {"new_ticket": "staff_new_ticket", "reply": "staff_reply_in", "assigned": "staff_assigned", "overdue": "staff_overdue",
        "escalated": "staff_escalated", "transferred": "staff_transferred", "user_closed": "staff_user_closed",
        "reopened": "staff_reopened", "sla_warning": "staff_sla_warning", "report": "staff_weekly_report"}


def render(t, lang: str, kind: str, params: dict) -> str:
    """The text of a notification in one language; `what` (first_response / resolution) is translated too."""
    values = {k: ("" if v is None else v) for k, v in (params or {}).items()}
    if "what" in values:
        values["what"] = t(lang, f"sla_{values['what']}") or values["what"]
    return t(lang, KEYS.get(kind, ""), **values) or " · ".join(str(v) for v in values.values())


def send(db, people: list[Staff], kind: str, ticket: Ticket | None, params: dict, exclude: int | None = None) -> None:
    from backend.services import content
    params = {k: (v[:200] if isinstance(v, str) else v) for k, v in params.items()}
    fallback = render(content.translator(db), "en", kind, params)  # for exports and old clients
    for person in people:
        if person is None or person.id == exclude:
            continue
        db.add(StaffNotification(staff_id=person.id, kind=kind, ticket_id=ticket.id if ticket else None, text=fallback[:500],
                                 data=params))
        if person.telegram_id and (person.notify or {}).get("telegram", True):
            queue.later(db, "workers.tasks.notify_staff", person.telegram_id, fallback[:1000], ticket.id if ticket else None,
                        kind, params)
