"""Acceptance criteria 5, 9 and 10: user profiles, analytics matching documented rules, audit log."""
from datetime import timedelta
from backend.models import AuditLog, Message, Ticket, TgUser
from backend.services import analytics
from tests.integration.test_tickets import new_ticket


def test_profile_tags_notes_direct_message_and_export(bot, admin, db):
    ticket = new_ticket(bot)
    assert admin.post("/api/admin/users/1001/tags", json={"tag": "VIP"}).json()["tags"] == ["vip"]
    assert admin.post("/api/admin/users/1001/notes", json={"text": "Called on Monday"}).status_code == 200
    sent = admin.post("/api/admin/users/1001/message", json={"text": "Your refund is ready"}).json()
    assert sent["ticket_id"] is None and sent["delivery"] == "queued"
    profile = admin.get("/api/admin/users/1001").json()
    assert profile["tickets"]["total"] == 1 and profile["tags"] == ["vip"] and profile["language"] is None
    assert profile["tg_language"] == "ru" and profile["notes"][0]["text"] == "Called on Monday"
    assert "phone" in profile  # super administrator sees supplied contacts
    export = admin.get("/api/admin/users/1001/export")
    assert export.status_code == 200 and export.json()["tickets"][0]["number"] == ticket["number"]
    assert db.query(AuditLog).filter_by(action="user.export").count() == 1
    assert admin.get("/api/admin/users", params={"q": "alice"}).json()["total"] == 1
    assert admin.get("/api/admin/users/1001/timeline").json()


def test_contacts_are_written_only_by_permitted_staff_and_audited(bot, admin, client, db):
    from tests.integration.conftest import login, make_staff
    new_ticket(bot)
    saved = admin.put("/api/admin/users/1001/contacts", json={"phone": "+972 50-123-4567", "email": "alice@example.com",
                                                              "timezone": "Asia/Jerusalem", "country": "Israel"})
    assert saved.status_code == 200 and saved.json()["phone"] == "+972501234567"
    assert admin.put("/api/admin/users/1001/contacts", json={"email": "nope"}).json()["detail"] == "bad_email"
    assert admin.put("/api/admin/users/1001/contacts", json={"timezone": "Mars/Base"}).json()["detail"] == "bad_timezone"
    assert db.query(AuditLog).filter_by(action="user.contacts").count() == 1
    make_staff(db, "agent-x", "agent")  # agents have no users.sensitive: they neither see nor edit contacts
    login(client, "agent-x")
    assert client.put("/api/admin/users/1001/contacts", json={"phone": "+15550000000"}).status_code == 403
    assert "phone" not in client.get("/api/admin/users/1001").json()


def test_language_filter_and_named_exports(bot, admin, db):
    from backend.models import Category
    category = db.query(Category).first()
    new_ticket(bot, category_id=category.id)  # Alice, Telegram language ru
    bot.post("/internal/register", json={"user_id": 2002, "first_name": "Bob", "language_code": "en"})
    users_all = admin.get("/api/admin/analytics/users").json()
    users_ru = admin.get("/api/admin/analytics/users", params={"language": "ru"}).json()
    users_fr = admin.get("/api/admin/analytics/users", params={"language": "fr"}).json()
    assert (users_all["total"], users_ru["total"], users_fr["total"]) == (2, 1, 0)
    assert users_ru["new"] == 1 and users_fr["active"] == 0
    export = admin.get("/api/admin/analytics/tickets/export", params={"format": "csv", "lang": "en"}).content.decode("utf-8-sig")
    assert category.name["en"] in export  # names, not ids


def test_anonymize_keeps_statistics(bot, admin, db):
    ticket = new_ticket(bot)
    assert admin.post("/api/admin/users/1001/anonymize").status_code == 200
    db.expire_all()
    user = db.get(TgUser, 1001)
    assert user.username is None and user.first_name is None and user.anonymized
    assert db.query(Message).filter_by(user_id=1001).first().text is None
    assert db.get(Ticket, ticket["id"]).number == ticket["number"]  # the ticket still counts in reports


def test_ticket_report_follows_the_rules(bot, admin, db):
    """Average/median first response and resolution, SLA compliance (docs/ANALYTICS.md)."""
    times = [(10, 60, False), (30, 120, False), (500, 1000, True)]  # minutes: first response, resolution, response breached
    for index, (response, resolution, breached) in enumerate(times):
        new_ticket(bot, text=f"t{index}", key=f"1001:{50 + index}")
    rows = db.query(Ticket).order_by(Ticket.id).all()
    for ticket, (response, resolution, breached) in zip(rows, times):
        ticket.first_response_at = ticket.created_at + timedelta(minutes=response)
        ticket.resolved_at = ticket.created_at + timedelta(minutes=resolution)
        ticket.response_breached = breached
        ticket.status = "resolved"
    db.commit()
    report = admin.get("/api/admin/analytics/tickets").json()
    assert report["total"] == 3 and report["resolved"] == 3
    assert report["first_response"] == {"average": round((10 + 30 + 500) * 60 / 3), "median": 30 * 60, "count": 3}
    assert report["resolution"]["median"] == 120 * 60
    assert report["sla_compliance"] == round(100 * 5 / 6, 1)  # 2 of 3 responses and 3 of 3 resolutions on time
    csv = admin.get("/api/admin/analytics/tickets/export", params={"format": "csv"})
    assert csv.status_code == 200 and csv.content.startswith("﻿".encode())
    xlsx = admin.get("/api/admin/analytics/tickets/export", params={"format": "xlsx"})
    assert xlsx.content[:2] == b"PK"
    pdf = admin.get("/api/admin/analytics/agents/export", params={"format": "pdf"})
    assert pdf.content[:4] == b"%PDF"


def test_overview_and_faq_report(bot, admin):
    new_ticket(bot)
    node = bot.get("/internal/content").json()["faq"]["children"]["root"][0]
    bot.post("/internal/event", json={"user_id": 1001, "kind": "faq_open", "ref_id": node})
    bot.post("/internal/event", json={"user_id": 1001, "kind": "search_miss", "data": {"query": "crypto wallet"}})
    overview = admin.get("/api/admin/overview").json()
    assert overview["tickets"]["new"] == 1 and overview["users"]["total"] == 1 and len(overview["trend"]) == 8
    faq = admin.get("/api/admin/analytics/faq").json()
    assert faq["top_questions"][0]["id"] == node and faq["missing"][0]["query"] == "crypto wallet"


def test_flatten_handles_nested_values():
    tables = analytics.flatten({"total": 2, "first_response": {"average": 1, "median": 1, "count": 2}, "by_status": {"new": 2},
                                "trend": [{"day": "2026-01-01", "created": 2}]})
    assert [t[0] for t in tables] == ["summary", "first_response", "by_status", "trend"]
    assert tables[0][2] == [["total", 2]]
