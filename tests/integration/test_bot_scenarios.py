"""The bot end to end: real aiogram dispatcher and handlers, real
backend and database through the internal API; only Telegram itself is replaced by a recorder of the API calls.

Each test plays a conversation the way Telegram delivers it — updates in, Bot API calls out — and checks both what
the user sees and what the backend stored.
"""
import asyncio
import time
from datetime import datetime, timezone
import httpx
import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText, SendMessage, SetMessageReaction
from aiogram.types import Chat, Message as TgMessage, ReplyKeyboardMarkup, Update, User
from sqlalchemy import func, select
from backend.database import session
from backend.main import app
from backend.models import Department, Message, Ticket, TgUser, UserEvent
from backend.services import faq, tickets
from bots.client import ApiClient
from bots.support import Context, dispatcher
from tests.integration.conftest import make_staff

ANN, OMAR, AGENT_TG = 501, 502, 777
RLM = "‏"


class TelegramRecorder(BaseSession):
    """Stands in for api.telegram.org: records every Bot API call and answers like Telegram would."""

    def __init__(self):
        super().__init__()
        self.calls: list = []
        self.next_id = 10_000

    def _message(self, method) -> TgMessage:
        self.next_id += 1
        chat_id = getattr(method, "chat_id", None) or 1
        return TgMessage(message_id=self.next_id, date=datetime.now(timezone.utc), chat=Chat(id=chat_id, type="private"),
                         text=getattr(method, "text", None))

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        returning = str(method.__returning__)
        if returning.startswith(("list", "typing.List")) or "list[" in returning:
            return [self._message(method)]
        if "Message" in returning:
            return self._message(method)
        if "User" in returning:
            return User(id=1, is_bot=True, first_name="Support", username="support_test_bot")
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self):
        pass


class Chatting:
    """One bot process talking to the test backend."""

    def __init__(self):
        self.telegram = TelegramRecorder()
        self.bot = Bot("123456:" + "A" * 35, session=self.telegram, default=DefaultBotProperties(parse_mode="HTML"))
        self.api = ApiClient(base_url="http://backend", transport=httpx.ASGITransport(app=app))
        self.ctx = Context(self.bot, self.api)
        self.dp = dispatcher(self.ctx, MemoryStorage())
        self.update_id = int(time.time())

    def _sender(self, user_id: int, language: str) -> dict:
        return {"id": user_id, "is_bot": False, "first_name": f"User{user_id}", "language_code": language}

    async def _feed(self, update: dict) -> list:
        start = len(self.telegram.calls)
        await self.dp.feed_update(self.bot, Update.model_validate(update, context={"bot": self.bot}))
        return self.telegram.calls[start:]

    def next_update(self) -> int:
        self.update_id += 1
        return self.update_id

    async def send(self, user_id: int, text: str | None = None, *, language: str = "en", update_id: int | None = None, **extra) -> list:
        number = update_id or self.next_update()
        message = {"message_id": number, "date": int(time.time()), "chat": {"id": user_id, "type": "private"},
                   "from": self._sender(user_id, language), **({"text": text} if text is not None else {}), **extra}
        return await self._feed({"update_id": number, "message": message})

    async def press(self, user_id: int, data: str, *, language: str = "en") -> list:
        number = self.next_update()
        return await self._feed({"update_id": number, "callback_query": {
            "id": str(number), "from": self._sender(user_id, language), "chat_instance": "test", "data": data,
            "message": {"message_id": number, "date": int(time.time()), "chat": {"id": user_id, "type": "private"}, "text": "…"}}})


def texts(calls: list) -> list[str]:
    return [c.text for c in calls if isinstance(c, (SendMessage, EditMessageText)) and c.text]


def said(calls: list, fragment: str) -> bool:
    return any(fragment in text for text in texts(calls))


@pytest.fixture
def chat(db):
    app.dependency_overrides[session] = lambda: db
    yield Chatting()
    app.dependency_overrides.clear()


def run(coroutine):
    return asyncio.run(coroutine)


async def open_ticket(chat: Chatting, db, user_id: int, text: str = "The app crashes on start") -> Ticket:
    """«Contact support» → Technical support → skip the category → describe."""
    await chat.press(user_id, "support")
    tech = db.scalar(select(Department).where(Department.name["en"].as_string() == "Technical support"))
    await chat.press(user_id, f"dep:{tech.id}")
    await chat.press(user_id, "cat:0")
    await chat.send(user_id, text)
    return db.scalar(select(Ticket).where(Ticket.user_id == user_id).order_by(Ticket.id.desc()))


# ---------------------------------------------------------------- webhook
def test_webhook_refuses_calls_without_the_secret(chat, db):
    from aiohttp.test_utils import TestClient as WebClient, TestServer
    from bots.runner import WEBHOOK_PATH, webhook_app
    secret, header = "s" * 32, "X-Telegram-Bot-Api-Secret-Token"
    update = {"update_id": chat.next_update(), "message": {"message_id": 1, "date": int(time.time()), "text": "/start",
              "chat": {"id": ANN, "type": "private"}, "from": {"id": ANN, "is_bot": False, "first_name": "Ann", "language_code": "en"}}}

    async def scenario():
        async with WebClient(TestServer(webhook_app(chat.bot, chat.dp, secret))) as web:
            forged = await web.post(WEBHOOK_PATH, json=update)
            wrong = await web.post(WEBHOOK_PATH, json=update, headers={header: "x" * 32})
            assert not chat.telegram.calls and db.get(TgUser, ANN) is None  # nothing reached the bot
            real = await web.post(WEBHOOK_PATH, json=update, headers={header: secret})
            for _ in range(50):  # handled in the background, like in production
                if chat.telegram.calls:
                    break
                await asyncio.sleep(0.1)
            return forged.status, wrong.status, real.status
    assert run(scenario()) == (401, 401, 200)
    assert said(chat.telegram.calls, "Welcome")


# ---------------------------------------------------------------- welcome, languages, FAQ
def test_start_welcomes_and_keeps_the_menu_under_the_input(chat, db):
    calls = run(chat.send(ANN, "/start"))
    assert said(calls, "Welcome")
    assert any(isinstance(getattr(c, "reply_markup", None), ReplyKeyboardMarkup) for c in calls)  # the persistent menu
    assert db.get(TgUser, ANN).tg_language == "en"


def test_right_to_left_language_gets_the_direction_mark(chat, db):
    calls = run(chat.send(OMAR, "/start", language="ar"))
    welcome = texts(calls)[0]
    assert welcome.startswith(RLM) and "Welcome" not in welcome


def test_faq_navigation_is_recorded(chat, db):
    async def scenario():
        root = await chat.press(ANN, "faq:root")
        snapshot = faq.latest(db).snapshot
        first = snapshot["children"]["root"][0] if "root" in snapshot["children"] else snapshot["children"][""][0]
        answer = await chat.press(ANN, f"faq:{first}")
        return root, answer, first
    root, answer, first = run(scenario())
    assert said(root, "Frequently asked questions")
    assert texts(answer)
    assert db.scalar(select(func.count()).select_from(UserEvent).where(UserEvent.user_id == ANN, UserEvent.kind == "faq_open",
                                                                       UserEvent.ref_id == first)) == 1


# ---------------------------------------------------------------- tickets
def test_contact_support_opens_one_ticket_and_follow_ups_join_it(chat, db):
    async def scenario():
        ticket = await open_ticket(chat, db, ANN)
        follow_up = await chat.send(ANN, "It started after the update")
        return ticket, follow_up
    ticket, follow_up = run(scenario())
    assert ticket.number and ticket.status == "new" and ticket.department_id is not None
    assert any(isinstance(c, SetMessageReaction) for c in follow_up)  # the quiet «✍» receipt
    stored = db.scalars(select(Message).where(Message.ticket_id == ticket.id, Message.direction == "in")).all()
    assert [m.text for m in stored] == ["The app crashes on start", "It started after the update"]


def test_an_album_opens_one_ticket(chat, db):
    async def scenario():
        await chat.press(ANN, "support")
        general = db.scalar(select(Department).where(Department.name["en"].as_string() == "General questions"))
        await chat.press(ANN, f"dep:{general.id}")
        photo = lambda n: [{"file_id": f"photo-{n}", "file_unique_id": f"u{n}", "width": 10, "height": 10}]
        for n in (1, 2, 3):
            await chat.send(ANN, None, photo=photo(n), media_group_id="album-1", **({"caption": "Screenshots"} if n == 1 else {}))
    run(scenario())
    assert db.scalar(select(func.count()).select_from(Ticket).where(Ticket.user_id == ANN)) == 1
    assert db.scalar(select(func.count()).select_from(Message).where(Message.user_id == ANN, Message.kind == "photo")) == 3


def test_a_retried_update_is_handled_once(chat, db):
    async def scenario():
        ticket = await open_ticket(chat, db, ANN)
        number = chat.next_update()
        await chat.send(ANN, "once", update_id=number)
        await chat.send(ANN, "once", update_id=number)  # Telegram delivered it again
        return ticket
    ticket = run(scenario())
    assert db.scalar(select(func.count()).select_from(Message).where(Message.ticket_id == ticket.id, Message.text == "once")) == 1


def test_problem_solved_closes_the_ticket(chat, db):
    async def scenario():
        ticket = await open_ticket(chat, db, ANN)
        await chat.press(ANN, f"solve:{ticket.id}")
        return ticket
    ticket = run(scenario())
    db.refresh(ticket)
    assert tickets.stage_of(db, ticket.status) == "closed"


# ---------------------------------------------------------------- agents in Telegram
def link_agent(db, role: str = "agent"):
    staff = make_staff(db, f"tg-{role}", role)
    staff.telegram_id = AGENT_TG
    db.commit()
    return staff


def test_agent_answers_from_the_notification(chat, fake_infrastructure, db):
    async def scenario():
        ticket = await open_ticket(chat, db, ANN)
        agent = link_agent(db)
        prompt = await chat.press(AGENT_TG, f"sr:{ticket.id}")
        sent = await chat.send(AGENT_TG, "Please update the app to 2.1")
        return ticket, agent, prompt, sent
    ticket, agent, prompt, sent = run(scenario())
    assert said(prompt, ticket.number) and said(prompt, "The app crashes on start")  # the agent sees the ticket first
    assert said(sent, "Sent to the user")
    reply = db.scalar(select(Message).where(Message.ticket_id == ticket.id, Message.direction == "out"))
    assert reply.text == "Please update the app to 2.1" and reply.staff_id == agent.id and reply.delivery == "queued"
    db.refresh(ticket)
    assert ticket.first_response_at is not None and ticket.assignee_id == agent.id  # counts for SLA, the agent takes it
    assert ("workers.tasks.send_message", reply.id) in fake_infrastructure


def test_agent_swipe_reply_and_note(chat, db):
    async def scenario():
        ticket = await open_ticket(chat, db, ANN)
        link_agent(db)
        notification = {"message_id": 1, "date": int(time.time()), "chat": {"id": AGENT_TG, "type": "private"},
                        "from": {"id": 1, "is_bot": True, "first_name": "Support"}, "text": f"{ticket.number} · new ticket",
                        "reply_markup": {"inline_keyboard": [[{"text": "Reply", "callback_data": f"sr:{ticket.id}"},
                                                              {"text": "Note", "callback_data": f"sn:{ticket.id}"}]]}}
        await chat.send(AGENT_TG, "Answer by swiping", reply_to_message=notification)
        await chat.press(AGENT_TG, f"sn:{ticket.id}")
        await chat.send(AGENT_TG, "Customer is on Android 9")
        return ticket
    ticket = run(scenario())
    rows = {m.direction: m.text for m in db.scalars(select(Message).where(Message.ticket_id == ticket.id, Message.direction.in_(("out", "note"))))}
    assert rows == {"out": "Answer by swiping", "note": "Customer is on Android 9"}


def test_agent_forwards_a_photo(chat, db):
    async def scenario():
        ticket = await open_ticket(chat, db, ANN)
        link_agent(db)
        await chat.press(AGENT_TG, f"sr:{ticket.id}")
        await chat.send(AGENT_TG, None, photo=[{"file_id": "agent-photo", "file_unique_id": "ap", "width": 10, "height": 10}],
                        caption="Tap here")
        return ticket
    ticket = run(scenario())
    reply = db.scalar(select(Message).where(Message.ticket_id == ticket.id, Message.direction == "out"))
    assert (reply.kind, reply.file_id, reply.text) == ("photo", "agent-photo", "Tap here")


def test_only_permitted_staff_answer_from_telegram(chat, db):
    async def scenario():
        ticket = await open_ticket(chat, db, ANN)
        stranger = await chat.press(OMAR, f"sr:{ticket.id}")  # a user, not staff
        link_agent(db, "content_manager")  # staff, but without tickets.reply
        manager = await chat.press(AGENT_TG, f"sr:{ticket.id}")
        await chat.send(AGENT_TG, "Should not arrive")
        return ticket, stranger, manager
    ticket, stranger, manager = run(scenario())
    assert said(stranger, "isn't available") and said(manager, "isn't available")
    assert db.scalar(select(func.count()).select_from(Message).where(Message.ticket_id == ticket.id, Message.direction == "out")) == 0


# ---------------------------------------------------------------- contact details
def test_user_shares_and_deletes_contact_details(chat, db):
    async def scenario():
        shown = await chat.send(ANN, "/contacts")
        await chat.send(ANN, None, contact={"phone_number": "972501234567", "first_name": "Ann", "user_id": ANN})
        foreign = await chat.send(ANN, None, contact={"phone_number": "15550001111", "first_name": "Bob", "user_id": 999})
        await chat.press(ANN, "contacts:email")
        wrong = await chat.send(ANN, "not an address")
        await chat.send(ANN, "ann@example.com")
        return shown, foreign, wrong
    shown, foreign, wrong = run(scenario())
    assert said(shown, "Your contact details")
    assert any(getattr(c.reply_markup, "keyboard", None) and c.reply_markup.keyboard[0][0].request_contact
               for c in shown if isinstance(c, SendMessage) and c.reply_markup)
    assert said(foreign, "your own phone number") and said(wrong, "doesn't look like an e-mail")
    user = db.get(TgUser, ANN)
    db.refresh(user)
    assert (user.phone, user.email) == ("+972501234567", "ann@example.com")
    run(chat.press(ANN, "contacts:delete"))
    db.refresh(user)
    assert user.phone is None and user.email is None
