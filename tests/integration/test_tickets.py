"""Acceptance criteria 3 and 4: users create tickets, send messages and attachments and receive replies;
agents reply from the dashboard and manage status, priority and assignment."""
from datetime import timedelta
from backend.models import Message, Ticket, TicketEvent
from tests.integration.conftest import login, make_staff

USER = {"user_id": 1001, "username": "alice", "first_name": "Alice", "language_code": "ru"}


def new_ticket(bot, text="My payment failed", key="1001:10", **extra):
    assert bot.post("/internal/register", json=USER).status_code == 200
    response = bot.post("/internal/tickets", json={"user_id": 1001, "text": text, "tg_message_id": 10, "dedupe_key": key, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_bot_creates_ticket_once_and_dashboard_sees_it(bot, admin, db, fake_infrastructure):
    first = new_ticket(bot)
    again = new_ticket(bot)  # the same Telegram update retried
    assert first["id"] == again["id"] and again["created"] is False
    assert first["number"] == f"T-{first['id']:06d}" and first["status"] == "new" and first["status_name"] == "Новое"
    assert db.query(Message).filter_by(ticket_id=first["id"], direction="in").count() == 1
    listed = admin.get("/api/admin/tickets", params={"q": "payment"}).json()
    assert listed["total"] == 1 and listed["items"][0]["user"]["name"] == "Alice"
    card = admin.get(f"/api/admin/tickets/{first['id']}").json()
    assert card["messages"][0]["text"] == "My payment failed" and card["events"][0]["kind"] == "created"
    assert card["due_response_at"] and card["allowed_statuses"]


def test_reply_status_and_user_follow_up(bot, admin, db, fake_infrastructure):
    ticket = new_ticket(bot)
    reply = admin.post(f"/api/admin/tickets/{ticket['id']}/reply", json={"text": "Please send the receipt"})
    assert reply.status_code == 200 and reply.json()["delivery"] == "queued"
    assert ("workers.tasks.send_message", reply.json()["id"]) in fake_infrastructure  # sent after the commit
    card = admin.get(f"/api/admin/tickets/{ticket['id']}").json()
    assert card["status"] == "open" and card["first_response_at"] and card["assignee_id"]  # first reply takes the ticket
    assert admin.post(f"/api/admin/tickets/{ticket['id']}/status", json={"value": "pending_user"}).status_code == 200
    system = db.query(Message).filter_by(ticket_id=ticket["id"], direction="system").one()
    assert "receipt" not in system.text and system.delivery == "queued"  # the user is told the ticket waits for them
    follow = bot.post(f"/internal/tickets/{ticket['id']}/message", json={"user_id": 1001, "kind": "photo", "file_id": "AgAC",
                                                                        "file_name": "receipt.jpg", "dedupe_key": "1001:11"})
    assert follow.status_code == 200 and follow.json()["status"] == "pending_agent"
    assert ("workers.tasks.copy_attachment",) == fake_infrastructure[-1][:1]
    assert admin.post(f"/api/admin/tickets/{ticket['id']}/status", json={"value": "resolved"}).status_code == 200
    resolved = db.query(Message).filter_by(ticket_id=ticket["id"], direction="system").order_by(Message.id.desc()).first()
    assert resolved.markup == "rating"
    assert bot.post(f"/internal/tickets/{ticket['id']}/rate", json={"user_id": 1001, "stars": 5}).json()["done"] is True
    assert bot.post(f"/internal/tickets/{ticket['id']}/rate", json={"user_id": 1001, "stars": 1}).json()["done"] is False


def test_transitions_are_enforced_and_recorded(bot, admin, db):
    ticket = new_ticket(bot)
    assert admin.post(f"/api/admin/tickets/{ticket['id']}/status", json={"value": "closed"}).status_code == 200
    refused = admin.post(f"/api/admin/tickets/{ticket['id']}/status", json={"value": "pending_user"})
    assert refused.status_code == 400 and refused.json()["detail"] == "transition_not_allowed"
    events = db.query(TicketEvent).filter_by(ticket_id=ticket["id"], kind="status").all()
    assert [(e.old, e.new, e.actor_name) for e in events] == [("new", "closed", "root")]


def test_priority_recalculates_sla_and_assignment_notifies(bot, admin, db):
    ticket = new_ticket(bot)
    before = admin.get(f"/api/admin/tickets/{ticket['id']}").json()["due_response_at"]
    after = admin.post(f"/api/admin/tickets/{ticket['id']}/priority", json={"value": "urgent"}).json()["due_response_at"]
    assert after < before
    agent = make_staff(db, "agent1", "agent")
    assert admin.post(f"/api/admin/tickets/{ticket['id']}/assign", json={"value": agent.id}).json()["assignee_id"] == agent.id
    login(admin, "agent1")
    bell = admin.get("/api/admin/notifications").json()
    assert bell["unread"] >= 1 and any(n["kind"] == "assigned" for n in bell["items"])


def test_agent_sees_only_permitted_tickets(bot, client, db):
    """Acceptance criterion 8: role-based permissions prevent access to other departments' tickets."""
    tech, billing = 2, 3  # seeded departments
    ticket = new_ticket(bot, department_id=billing)
    make_staff(db, "techie", "agent", departments=[tech])
    login(client, "techie")
    assert client.get("/api/admin/tickets").json()["total"] == 0
    assert client.get(f"/api/admin/tickets/{ticket['id']}").status_code == 404
    assert client.post(f"/api/admin/tickets/{ticket['id']}/assign", json={"value": None}).status_code == 403  # no tickets.assign
    make_staff(db, "biller", "agent", departments=[billing])
    login(client, "biller")
    assert client.get("/api/admin/tickets").json()["total"] == 1


def test_internal_notes_hidden_without_permission(bot, admin, db):
    ticket = new_ticket(bot)
    assert admin.post(f"/api/admin/tickets/{ticket['id']}/note", json={"text": "VIP customer"}).status_code == 200
    db.query(Ticket).filter_by(id=ticket["id"]).update({"assignee_id": None})
    db.commit()
    make_staff(db, "reader", "reporting_admin")
    login(admin, "reader")
    assert admin.get(f"/api/admin/tickets/{ticket['id']}").status_code == 403


def test_user_closes_and_reopens(bot, db):
    ticket = new_ticket(bot)
    closed = bot.post(f"/internal/tickets/{ticket['id']}/close", json={"user_id": 1001}).json()
    assert closed["done"] and closed["stage"] == "closed" and closed["ask_rating"]  # «Problem solved» closes the ticket
    assert bot.post(f"/internal/tickets/{ticket['id']}/close", json={"user_id": 1001}).json()["done"] is False
    reopened = bot.post(f"/internal/tickets/{ticket['id']}/reopen", json={"user_id": 1001}).json()
    assert reopened["result"] == "ok" and reopened["status"] == "reopened"
    assert bot.post(f"/internal/tickets/{ticket['id']}", json={"user_id": 999}).status_code == 404  # not their ticket


def test_banned_user_and_flood_are_refused(bot, admin, db):
    ticket = new_ticket(bot)
    for index in range(19):
        bot.post(f"/internal/tickets/{ticket['id']}/message", json={"user_id": 1001, "text": f"m{index}", "dedupe_key": f"1001:{100 + index}"})
    flood = bot.post(f"/internal/tickets/{ticket['id']}/message", json={"user_id": 1001, "text": "spam", "dedupe_key": "1001:999"})
    assert flood.status_code == 429 and flood.json()["detail"] == "rate_limit"
    assert admin.post("/api/admin/users/1001/ban", json={"reason": "spam"}).status_code == 200
    assert bot.post("/internal/register", json=USER).json()["banned"] is True


def test_sla_pauses_while_waiting_for_the_user(bot, admin, db):
    assert admin.patch("/api/admin/settings/general", json={"sla_pause_pending_user": True}).status_code == 200
    ticket = new_ticket(bot)
    row = db.get(Ticket, ticket["id"])
    due = row.due_resolution_at
    admin.post(f"/api/admin/tickets/{ticket['id']}/reply", json={"text": "Which card did you use?"})
    admin.post(f"/api/admin/tickets/{ticket['id']}/status", json={"value": "pending_user"})
    db.refresh(row)
    assert row.sla_paused_at is not None
    row.sla_paused_at -= timedelta(hours=2)  # the user answered two hours later
    db.commit()
    bot.post(f"/internal/tickets/{ticket['id']}/message", json={"user_id": 1001, "text": "Visa", "dedupe_key": "1001:77"})
    db.refresh(row)
    assert row.status == "pending_agent" and row.sla_paused_at is None
    assert row.sla_paused_seconds >= 7200 and row.due_resolution_at - due == timedelta(seconds=row.sla_paused_seconds)


def test_business_hours_move_open_tickets(bot, admin, db):
    ticket = new_ticket(bot)
    before = db.get(Ticket, ticket["id"]).due_response_at
    closed = {"enabled": True, "timezone": "UTC", "days": {d: (["09:00", "10:00"] if d == "mon" else None) for d in
                                                            ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}}
    assert admin.patch("/api/admin/settings/general", json={"business_hours": closed, "sla_business_hours": True}).status_code == 200
    db.expire_all()
    after = db.get(Ticket, ticket["id"]).due_response_at
    assert after > before and after.weekday() == 0 and 9 <= after.hour <= 10  # 4 business hours over several Mondays


def test_staff_close_and_reopen(bot, admin, db):
    ticket = new_ticket(bot)
    assert admin.post(f"/api/admin/tickets/{ticket['id']}/close", json={"comment": "done"}).json()["status"] == "closed"
    told = db.query(Message).filter_by(ticket_id=ticket["id"], direction="system").one()
    assert ticket["number"] in told.text  # the user hears that the ticket is closed
    assert admin.post(f"/api/admin/tickets/{ticket['id']}/reopen", json={}).json()["status"] == "reopened"
    agent = make_staff(db, "closer", "agent")
    db.get(Ticket, ticket["id"]).assignee_id = agent.id
    db.commit()
    login(admin, "closer")
    assert admin.post(f"/api/admin/tickets/{ticket['id']}/close", json={}).status_code == 200
    assert admin.post(f"/api/admin/tickets/{ticket['id']}/reopen", json={}).status_code == 403  # agents have no tickets.reopen


def test_album_opens_one_ticket(bot, db):
    """Telegram sends an album as several messages; all of them land in one ticket, each stored once."""
    bot.post("/internal/register", json=USER)
    for message_id in (20, 21, 22, 21):  # 21 retried
        body = {"user_id": 1001, "kind": "photo", "file_id": f"F{message_id}", "file_name": "p.jpg", "tg_message_id": message_id,
                "dedupe_key": f"1001:{message_id}", "ticket_key": "1001:album-7"}
        assert bot.post("/internal/tickets", json=body).status_code == 200
    assert db.query(Ticket).count() == 1
    assert db.query(Message).filter_by(direction="in").count() == 3


def test_bot_requires_service_key(client):
    assert client.post("/internal/register", json=USER).status_code == 401
    client.headers["X-Service-Key"] = "wrong"
    assert client.get("/internal/content").status_code == 401
