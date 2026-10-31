"""Delivery, announcements and recovery."""
import pytest
from sqlalchemy.orm import sessionmaker
from backend.models import Campaign, CampaignDelivery, Message, TgUser
from backend.services import campaigns
from workers import tasks
from workers.telegram import TelegramError, TelegramRetry
from tests.integration.test_tickets import new_ticket


class FakeTelegram:
    """Records Bot API calls; `fail` maps a chat id to an exception to raise."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail or {}

    def execute(self, chat_id, calls, asset_source):
        error = self.fail.get(chat_id)
        if error:
            raise error
        self.calls.append((chat_id, calls))
        return [{"message_id": len(self.calls)}]

    def call(self, method, data, files=None):
        self.calls.append((data.get("chat_id"), method))
        return {"message_id": 1}


@pytest.fixture
def worker_db(db, monkeypatch):
    """The worker functions open their own sessions on the test database."""
    monkeypatch.setattr(tasks, "Session", sessionmaker(db.get_bind(), expire_on_commit=False))
    return db


def test_reply_is_delivered_once(bot, admin, worker_db):
    ticket = new_ticket(bot)
    message_id = admin.post(f"/api/admin/tickets/{ticket['id']}/reply", json={"text": "Hello <script>"}).json()["id"]
    telegram = FakeTelegram()
    assert tasks.deliver(message_id, telegram) == "sent"
    assert tasks.deliver(message_id, telegram) == "sent"  # a retried task does not send again
    assert len(telegram.calls) == 1
    text = telegram.calls[0][1][0]["data"]["text"]
    assert "&lt;script&gt;" in text and "root" in text and ticket["number"] in text  # agent text is escaped, signed and numbered


def test_agent_file_from_telegram_is_sent_by_file_id(bot, worker_db):
    from tests.integration.conftest import make_staff
    ticket = new_ticket(bot)
    agent = make_staff(worker_db, "tg-agent", "agent")
    agent.telegram_id = 4242
    worker_db.commit()
    stored = bot.post("/internal/staff/message", json={"telegram_id": 4242, "ticket_id": ticket["id"], "kind": "document",
                                                       "text": "The manual", "file_id": "BQAC-file", "file_name": "manual.pdf",
                                                       "dedupe_key": "4242:9"}).json()
    again = bot.post("/internal/staff/message", json={"telegram_id": 4242, "ticket_id": ticket["id"], "kind": "document",
                                                      "text": "The manual", "file_id": "BQAC-file", "dedupe_key": "4242:9"}).json()
    assert stored["stored"] and not again["stored"]  # a retried update is not answered twice
    message = worker_db.query(Message).filter_by(ticket_id=ticket["id"], direction="out").one()
    telegram = FakeTelegram()
    assert tasks.deliver(message.id, telegram) == "sent"
    call = telegram.calls[0][1][0]
    assert call["method"] == "sendDocument" and call["data"]["document"] == "BQAC-file" and "asset" not in call


def test_staff_notification_offers_reply_and_note(bot, worker_db, monkeypatch):
    ticket = new_ticket(bot)
    rows = tasks.staff_buttons(1001, ticket["id"])["inline_keyboard"]
    assert [b["callback_data"] for b in rows[0]] == [f"sr:{ticket['id']}", f"sn:{ticket['id']}"]
    assert len(rows) == 1  # no dashboard link to a plain-http address: Telegram refuses those
    assert tasks.staff_buttons(1001, None) is None


def test_blocked_user_is_marked(bot, admin, worker_db):
    ticket = new_ticket(bot)
    message_id = admin.post(f"/api/admin/tickets/{ticket['id']}/reply", json={"text": "Hi"}).json()["id"]
    assert tasks.deliver(message_id, FakeTelegram({1001: TelegramError("Forbidden: bot was blocked by the user", 403)})) == "blocked"
    worker_db.expire_all()
    assert worker_db.get(TgUser, 1001).bot_blocked is True
    assert admin.get(f"/api/admin/tickets/{ticket['id']}").json()["messages"][-1]["delivery"] == "blocked"


def test_temporary_failure_is_retried(bot, admin, worker_db):
    ticket = new_ticket(bot)
    message_id = admin.post(f"/api/admin/tickets/{ticket['id']}/reply", json={"text": "Hi"}).json()["id"]
    with pytest.raises(TelegramRetry):
        tasks.deliver(message_id, FakeTelegram({1001: TelegramRetry(5)}))
    worker_db.expire_all()
    assert worker_db.get(Message, message_id).delivery == "queued"
    assert tasks.deliver(message_id, FakeTelegram()) == "sent"


def make_users(db, count=5):
    for index in range(count):
        db.add(TgUser(id=5000 + index, first_name=f"U{index}", language="ru" if index % 2 else "en"))
    db.commit()


def test_campaign_reaches_segment_without_duplicates(admin, worker_db, fake_infrastructure):
    make_users(worker_db)
    worker_db.get(TgUser, 5004).campaigns_opt_out = True  # /stop
    worker_db.commit()
    body = {"name": "News", "content": {"blocks": [{"type": "text", "text": {"en": "Hello", "ru": "Привет"}}]},
            "filters": {"languages": ["ru", "en"]}}
    assert admin.post("/api/admin/campaigns/count", json={"filters": body["filters"]}).json()["recipients"] == 4
    campaign = admin.post("/api/admin/campaigns", json=body).json()
    assert admin.post(f"/api/admin/campaigns/{campaign['id']}/start").json()["recipients"] == 4
    telegram = FakeTelegram({5000: TelegramError("Forbidden: bot was blocked by the user", 403)})
    assert tasks.run_campaign(campaign["id"], telegram, sleep=lambda _: None) == "done"
    assert tasks.run_campaign(campaign["id"], telegram, sleep=lambda _: None) == "done"  # restarted: nothing sent again
    texts = {chat: calls[0]["data"]["text"] for chat, calls in telegram.calls}
    assert texts == {5001: "Привет", 5002: "Hello", 5003: "Привет"}
    stats = admin.get(f"/api/admin/campaigns/{campaign['id']}").json()["stats"]
    assert stats["sent"] == 3 and stats["blocked"] == 1


def test_flood_control_pauses_without_losing_recipients(admin, worker_db):
    make_users(worker_db, 2)
    campaign = campaigns.save(worker_db, None, {"name": "N", "content": {"blocks": [{"type": "text", "text": {"en": "x"}}]}},
                              type("Actor", (), {"username": "root"})())
    worker_db.commit()
    campaigns.start(worker_db, campaign, None)
    worker_db.commit()
    waits = []

    class Flood(FakeTelegram):
        def execute(self, chat_id, calls, asset_source):
            if not waits:
                raise TelegramRetry(3)
            return super().execute(chat_id, calls, asset_source)
    telegram = Flood()
    assert tasks.run_campaign(campaign.id, telegram, sleep=waits.append) == "done"
    assert 3 in waits and len(telegram.calls) == 2


def test_reconcile_restarts_lost_messages(bot, admin, worker_db, fake_infrastructure):
    ticket = new_ticket(bot)
    message_id = admin.post(f"/api/admin/tickets/{ticket['id']}/reply", json={"text": "Hi"}).json()["id"]
    message = worker_db.get(Message, message_id)
    message.created_at = message.created_at.replace(year=message.created_at.year - 1)
    worker_db.commit()
    fake_infrastructure.clear()
    assert tasks.reconcile_now()["messages"] == 1
    assert ("workers.tasks.send_message", message_id) in fake_infrastructure


def test_paused_campaign_stops(admin, worker_db):
    make_users(worker_db, 3)
    campaign = admin.post("/api/admin/campaigns", json={"name": "P", "content": {"blocks": [{"type": "text", "text": {"en": "x"}}]}}).json()
    admin.post(f"/api/admin/campaigns/{campaign['id']}/start")
    assert admin.post(f"/api/admin/campaigns/{campaign['id']}/pause").json()["status"] == "paused"
    assert tasks.run_campaign(campaign["id"], FakeTelegram(), sleep=lambda _: None) == "paused"
    assert worker_db.query(CampaignDelivery).filter_by(campaign_id=campaign["id"], status="pending").count() == 3
    assert admin.post(f"/api/admin/campaigns/{campaign['id']}/cancel").json()["status"] == "cancelled"
    assert worker_db.get(Campaign, campaign["id"]).status == "cancelled"
