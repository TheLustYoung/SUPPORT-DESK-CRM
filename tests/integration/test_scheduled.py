"""Scheduled jobs and translated staff notifications."""
from datetime import datetime, timedelta, timezone
from backend.models import Message, StaffNotification, Ticket, TicketEvent, TgUser, now
from backend.services import content, digest, notify, options, tickets
from tests.integration.test_tickets import new_ticket
from tests.integration.test_workers import worker_db  # noqa: F401 (fixture)


def test_agent_is_warned_once_before_the_sla_deadline(bot, admin, db):
    ticket = db.get(Ticket, new_ticket(bot)["id"])
    ticket.created_at = now() - timedelta(minutes=90)
    ticket.due_response_at = now() + timedelta(minutes=10)  # ~90 % of the time has passed
    db.commit()
    assert tickets.warn_sla(db) == 1
    assert tickets.warn_sla(db) == 0  # once per target
    warning = db.query(StaffNotification).filter_by(kind="sla_warning").one()
    assert warning.data["what"] == "first_response" and 1 <= warning.data["minutes"] <= 10
    options.put(db, "sla_warning_percent", 0)
    ticket.due_resolution_at = now() + timedelta(minutes=1)
    assert tickets.warn_sla(db) == 0  # switched off


def test_user_is_reminded_once_while_the_ticket_waits_for_them(bot, admin, db):
    created = new_ticket(bot)
    assert admin.post(f"/api/admin/tickets/{created['id']}/status", json={"value": "pending_user"}).status_code == 200
    assert tickets.remind_users(db) == 0  # not yet: 24 hours by default
    ticket = db.get(Ticket, created["id"])
    long_ago = now() - timedelta(hours=25)
    for event in db.query(TicketEvent).filter_by(ticket_id=ticket.id):
        event.created_at = long_ago
    ticket.created_at = long_ago
    db.commit()
    assert tickets.remind_users(db) == 1
    assert tickets.remind_users(db) == 0
    reminder = db.query(Message).filter_by(ticket_id=ticket.id, direction="system").order_by(Message.id.desc()).first()
    assert ticket.number in reminder.text and "ждёт вашего ответа" in reminder.text  # the user's language (ru)
    bot.post(f"/internal/tickets/{ticket.id}/message", json={"user_id": 1001, "text": "Here it is", "dedupe_key": "1001:11"})
    db.refresh(ticket)
    assert ticket.reminded_at is None and tickets.stage_of(db, ticket.status) == "pending_agent"  # the next wait gets its own


def test_weekly_summary_goes_out_once_on_monday_morning(bot, admin, db):
    new_ticket(bot)
    jerusalem = timezone(timedelta(hours=2))
    sunday = datetime(2026, 11, 1, 20, 0, tzinfo=jerusalem)
    monday_early, monday = sunday + timedelta(hours=11), sunday + timedelta(hours=14)  # 07:00 and 10:00
    assert not digest.weekly(db, sunday) and not digest.weekly(db, monday_early)
    assert digest.weekly(db, monday)
    assert not digest.weekly(db, monday + timedelta(hours=3)) and not digest.weekly(db, monday + timedelta(days=1))  # once a week
    report = db.query(StaffNotification).filter_by(kind="report").one()  # the super administrator sees reports
    assert report.data["period"] == "26.10–01.11" and {"created", "resolved", "open", "overdue", "sla", "csat"} <= report.data.keys()


def test_weekly_summary_catches_up_on_tuesday_with_the_same_week(admin, db):
    tuesday = datetime(2026, 11, 3, 8, 0, tzinfo=timezone(timedelta(hours=2)))  # the server was down on Monday
    assert digest.weekly(db, tuesday)
    assert db.query(StaffNotification).filter_by(kind="report").one().data["period"] == "26.10–01.11"
    assert not digest.weekly(db, tuesday + timedelta(days=1))  # Wednesday: nothing more


def test_staff_notifications_are_translated(bot, admin, db):
    created = new_ticket(bot)
    note = db.query(StaffNotification).filter_by(ticket_id=created["id"], kind="new_ticket").one()
    assert note.data == {"number": created["number"], "user": "Alice", "subject": "My payment failed"}
    t = content.translator(db)
    overdue = {"number": created["number"], "what": "first_response"}
    assert notify.render(t, "en", "overdue", overdue) == f"⏰ Ticket {created['number']} is overdue (first response)."
    assert "первый ответ" in notify.render(t, "ru", "overdue", overdue)
    assert notify.render(t, "ar", "new_ticket", note.data).startswith("‏")  # right-to-left mark


def test_worker_writes_to_staff_in_their_bot_language(bot, worker_db):
    from workers import tasks
    bot.post("/internal/register", json={"user_id": 555, "first_name": "Agent", "language_code": "ru"})
    text = tasks.staff_text(555, "fallback", "reopened", {"number": "T-000007", "user": "Bob"})
    assert text.endswith("Bob снова открыл тикет T-000007")
    assert tasks.staff_text(555, "old queued text", None, None) == "old queued text"


def test_ticket_search_finds_the_last_name(bot, admin, db):
    bot.post("/internal/register", json={"user_id": 1001, "first_name": "Alice", "last_name": "Kowalski", "language_code": "ru"})
    new_ticket(bot)
    db.get(TgUser, 1001).last_name = "Kowalski"
    db.commit()
    assert admin.get("/api/admin/tickets", params={"q": "kowal"}).json()["total"] == 1
