"""The support bot: welcome message and main menu, the FAQ tree, «Contact support» (department →
category → description with attachments), «My tickets», language choice, free-text FAQ search, announcements
opt-out (/stop, /news) and /link for staff notifications.

Rules learned on the media platform: filters are combined with or_f (never `|`), every handler runs inside `guard`
so the user always gets an answer, retried Telegram updates are ignored, and the dialogue state lives in Redis so a
restart does not lose a half-written ticket.
"""
import asyncio
import contextlib
import logging
from datetime import datetime
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
                           ReactionTypeEmoji, ReplyKeyboardMarkup, Update)
from backend.services import options, render
from backend.services.i18n import escape
from backend.services.rich import to_telegram as render_text
from backend.services.search import wants_person
from bots.client import ApiClient, ApiError
from bots.content import Content, ContentCache
from bots.sender import Sender

log = logging.getLogger("bot")
MEDIA = ("photo", "video", "voice", "audio", "document", "animation", "video_note")
FILE_ERRORS = {"file_too_large", "unsupported", "rate_limit", "banned", "ticket_closed"}


class Support(StatesGroup):
    department = State()
    category = State()
    describe = State()
    writing = State()  # the next message goes to a chosen ticket


class StaffReply(StatesGroup):
    """A linked staff member answering a ticket from its Telegram notification."""
    reply = State()
    note = State()


class ContactDetails(StatesGroup):
    email = State()


STAFF_DENIED = {"not_staff", "not_found", "no_permission", "unsupported"}


def button(text: str, data: str | None = None, url: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url) if url else InlineKeyboardButton(text=text, callback_data=data)


def payload(message: Message) -> dict:
    """What a user's Telegram message carries: kind, text or caption, attachment."""
    kind = next((k for k in MEDIA if getattr(message, k, None)), "text" if message.text else "other")
    item = getattr(message, kind, None) if kind in MEDIA else None
    if kind == "photo":
        item = message.photo[-1]
    return {"kind": "video" if kind == "video_note" else kind, "text": message.text or message.caption,
            "file_id": getattr(item, "file_id", None), "file_name": getattr(item, "file_name", None) or (f"{kind}.jpg" if kind == "photo" else None),
            "file_size": getattr(item, "file_size", None), "tg_message_id": message.message_id,
            "dedupe_key": f"{message.chat.id}:{message.message_id}",
            # one ticket per album: its photos share a media group
            "ticket_key": f"{message.chat.id}:{message.media_group_id or message.message_id}"}


def staff_notification(message: Message | None) -> tuple[int, str] | None:
    """(ticket id, "reply") when `message` is a ticket notification of this bot — the one with «Reply» / «Note» buttons.
    A staff member's Telegram reply to it is their answer to the user; the backend checks who may do that."""
    markup = getattr(message, "reply_markup", None) if message is not None else None
    for row in getattr(markup, "inline_keyboard", None) or []:
        for item in row:
            data = item.callback_data or ""
            if data.startswith("sr:") and data[3:].isdigit():
                return int(data[3:]), "reply"
    return None


class Context:
    def __init__(self, bot: Bot, api: ApiClient):
        self.bot, self.api, self.cache, self.sender = bot, api, ContentCache(api), Sender(bot, api)
        # Updates of one user are handled one after another: an album arrives as several messages at once, and in
        # parallel they would all reach the «describe» step and open several tickets.
        self.locks: dict[int, asyncio.Lock] = {}

    def lock(self, user_id: int) -> asyncio.Lock:
        if len(self.locks) > 10000:
            self.locks = {k: v for k, v in self.locks.items() if v.locked()}
        return self.locks.setdefault(user_id, asyncio.Lock())

    @staticmethod
    def identity(user) -> dict:
        return {"user_id": user.id, "username": user.username, "first_name": user.first_name, "last_name": user.last_name,
                "language_code": user.language_code, "is_premium": bool(user.is_premium)}

    # ------------------------------------------------------------ screens
    def menu_markup(self, c: Content, lang: str) -> InlineKeyboardMarkup:
        rows = []
        for item in c.menu:
            label = c.name(item["label"], lang) or c.t.label(lang, f"menu_{item['action']}")
            if not label:
                continue
            if item["action"] == "url":
                rows.append([button(label, url=item["value"])])
            else:
                target = {"faq": "faq:root", "support": "support", "tickets": "tickets", "language": "lang", "contacts": "contacts",
                          "faq_node": f"faq:{item['value']}", "content": f"page:{item['id']}"}[item["action"]]
                rows.append([button(label, target)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def label_of(c: Content, item: dict, lang: str) -> str:
        return c.name(item["label"], lang) or c.t.label(lang, f"menu_{item['action']}")

    def keyboard(self, c: Content, lang: str) -> ReplyKeyboardMarkup:
        """The menu kept under the input field: always one tap away (it also replaces a keyboard left by an older bot)."""
        labels = [self.label_of(c, item, lang) for item in c.menu if item["action"] != "url"]
        buttons = [KeyboardButton(text=label) for label in labels if label]
        return ReplyKeyboardMarkup(keyboard=[buttons[i:i + 2] for i in range(0, len(buttons), 2)], resize_keyboard=True, is_persistent=True)

    def menu_item(self, c: Content, text: str | None) -> dict | None:
        """The menu item whose label (in any language) the user tapped on the keyboard."""
        text = (text or "").strip()
        if not text:
            return None
        for item in c.menu:
            if item["action"] != "url" and any(self.label_of(c, item, code) == text for code in c.codes):
                return item
        return None

    async def open_item(self, item: dict, message: Message, c: Content, lang: str, state: FSMContext, user_id: int) -> None:
        await state.clear()
        action = item["action"]
        if action == "faq":
            await self.faq(message, c, lang, user_id, None, state=state)
        elif action == "faq_node":
            await self.faq(message, c, lang, user_id, int(item["value"]), state=state)
        elif action == "support":
            await self.start_support(message, c, lang, state)
        elif action == "tickets":
            await self.tickets(message, c, lang, user_id)
        elif action == "language":
            await self.languages(message, c, lang)
        elif action == "contacts":
            await self.contacts(message, c, lang, user_id)
        elif action == "content":
            await self.sender.run(message.chat.id, render.plan(item["content"], lang, c.default, rtl=c.rtl(lang)))

    async def welcome(self, message: Message, c: Content, lang: str, name: str) -> None:
        menu = [[b.model_dump(exclude_none=True) for b in row] for row in self.menu_markup(c, lang).inline_keyboard]
        if c.welcome.get("blocks"):
            calls = render.plan(c.welcome, lang, c.default, rtl=c.rtl(lang), extra_buttons=menu, buttons_text=c.t(lang, "menu_title"))
        else:
            text = c.t(lang, "welcome", name=name, company=c.settings.get("company_name", ""))
            calls = render.plan({"blocks": [{"type": "text", "text": {lang: text}}]}, lang, c.default, extra_buttons=menu)
        await self.sender.run(message.chat.id, calls)
        await message.answer(c.t(lang, "keyboard_hint"), reply_markup=self.keyboard(c, lang))

    async def main_menu(self, message: Message, c: Content, lang: str, edit: bool = False) -> None:
        text, markup = c.t(lang, "menu_title"), self.menu_markup(c, lang)
        if edit and message.text:
            with contextlib.suppress(Exception):
                await message.edit_text(text, reply_markup=markup)
                return
        await message.answer(text, reply_markup=markup)

    def nav(self, c: Content, lang: str, parent: int | None) -> list[list[dict]]:
        back = f"faq:{parent}" if parent is not None else "faq:root"
        return [[{"text": c.t.label(lang, "btn_back"), "callback_data": back}, {"text": c.t.label(lang, "btn_main_menu"), "callback_data": "menu"}]]

    async def faq(self, message: Message, c: Content, lang: str, user_id: int, node_id: int | None, edit: bool = False, depth: int = 0,
                  state: FSMContext | None = None) -> None:
        """A FAQ category (its questions as buttons) or an answer with its content, «Contact support», Back and Main menu."""
        if node_id is None:
            children = c.tree.children(None)
            if not children:
                await message.answer(c.t(lang, "faq_empty"), reply_markup=self.menu_markup(c, lang))
                return
            rows = [[button(c.name(n["title"], lang), f"faq:{n['id']}")] for n in children]
            rows.append([button(c.t.label(lang, "btn_main_menu"), "menu")])
            return await self._show(message, c.t(lang, "faq_title"), InlineKeyboardMarkup(inline_keyboard=rows), edit)
        node = c.tree.get(node_id)
        if node is None:
            return await self.faq(message, c, lang, user_id, None, edit, state=state)
        await self.api.post("event", {"user_id": user_id, "kind": "faq_open", "ref_id": node_id, "data": {"path": c.tree.path(node_id)}})
        redirect, value = node.get("redirect"), node.get("redirect_value")
        if redirect == "node" and value.isdigit() and depth < 5:
            return await self.faq(message, c, lang, user_id, int(value), edit, depth + 1, state=state)
        if redirect == "menu":
            return await self.main_menu(message, c, lang, edit)
        if redirect == "support":
            return await self.start_support(message, c, lang, state, faq_node=node)
        extra = []
        if redirect == "url" and value:
            extra.append([{"text": c.name(node["title"], lang), "url": value}])
        children = c.tree.children(node_id)
        extra += [[{"text": c.name(n["title"], lang), "callback_data": f"faq:{n['id']}"}] for n in children]
        if node.get("allow_ticket") and not children:
            extra.append([{"text": c.t.label(lang, "btn_contact"), "callback_data": f"support:{node_id}"}])
        extra += self.nav(c, lang, node.get("parent_id"))
        if not (node.get("content") or {}).get("blocks"):  # a category: its title, sub-questions as buttons
            title = f"<b>{escape(c.name(node['title'], lang))}</b>\n{c.t(lang, 'faq_choose')}"
            return await self._show(message, c.t.direction(lang, title), InlineKeyboardMarkup.model_validate({"inline_keyboard": extra}), edit)
        calls = render.plan(node["content"], lang, c.default, rtl=c.rtl(lang), extra_buttons=extra, buttons_text=c.t(lang, "faq_choose"))
        await self.sender.run(message.chat.id, calls)

    @staticmethod
    async def _show(message: Message, text: str, markup: InlineKeyboardMarkup, edit: bool) -> None:
        if edit and message.text:
            with contextlib.suppress(Exception):
                await message.edit_text(text, reply_markup=markup)
                return
        await message.answer(text, reply_markup=markup)

    async def start_support(self, message: Message, c: Content, lang: str, state: FSMContext, faq_node: dict | None = None) -> None:
        """«Contact support»: department (if more than one), category (if any), then the description."""
        await state.clear()
        data = {"faq_node_id": faq_node["id"] if faq_node else None, "department_id": (faq_node or {}).get("department_id"),
                "category_id": (faq_node or {}).get("category_id")}
        cancel = [button(c.t.label(lang, "btn_cancel"), "cancel")]
        if data["department_id"] is None and c.settings.get("ask_department", True) and len(c.departments) > 1:
            await state.set_state(Support.department)
            await state.update_data(**data)
            rows = [[button(c.name(d["name"], lang), f"dep:{d['id']}")] for d in c.departments] + [cancel]
            await message.answer(c.t(lang, "support_department"), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            return
        if data["department_id"] is None and len(c.departments) == 1:
            data["department_id"] = c.departments[0]["id"]
        await self.ask_category(message, c, lang, state, data)

    async def ask_category(self, message: Message, c: Content, lang: str, state: FSMContext, data: dict) -> None:
        cancel = [button(c.t.label(lang, "btn_cancel"), "cancel")]
        categories = c.categories_of(data.get("department_id"))
        if data.get("category_id") is None and c.settings.get("ask_category", True) and categories:
            await state.set_state(Support.category)
            await state.update_data(**data)
            rows = [[button(c.name(x["name"], lang), f"cat:{x['id']}")] for x in categories]
            rows += [[button(c.t.label(lang, "btn_skip"), "cat:0")], cancel]
            await message.answer(c.t(lang, "support_category"), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            return
        await state.set_state(Support.describe)
        await state.update_data(**data)
        await message.answer(c.t(lang, "support_describe"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[cancel]))

    async def tickets(self, message: Message, c: Content, lang: str, user_id: int, edit: bool = False) -> None:
        items = await self.api.post("tickets/list", {"user_id": user_id})
        menu = [button(c.t.label(lang, "btn_main_menu"), "menu")]
        if not items:
            return await self._show(message, c.t(lang, "tickets_empty"), InlineKeyboardMarkup(inline_keyboard=[
                [button(c.t.label(lang, "btn_contact"), "support")], menu]), edit)
        rows = [[button(f"{t['number']} · {t['status_name']} · {t['subject'][:30]}", f"t:{t['id']}")] for t in items[:15]]
        await self._show(message, c.t(lang, "tickets_title"), InlineKeyboardMarkup(inline_keyboard=rows + [menu]), edit)

    async def ticket(self, message: Message, c: Content, lang: str, user_id: int, ticket_id: int, edit: bool = True) -> None:
        t = await self.api.post(f"tickets/{ticket_id}", {"user_id": user_id})
        lines = []
        for m in t["messages"]:
            who = c.t.label(lang, "you") if m["direction"] == "in" else (m["author"] or c.t.label(lang, "support_name"))
            lines.append(f"<b>{escape(who)}:</b> {escape((m['text'] or '[' + m['kind'] + ']')[:300])}")
        history = "\n".join(lines) or c.t(lang, "ticket_history_empty")
        when = lambda value: datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
        text = c.t(lang, "ticket_detail", number=t["number"], status=t["status_name"], created=when(t["created_at"]),
                   updated=when(t["updated_at"]), history="") + history
        rows = []
        if t["stage"] != "closed":
            rows.append([button(c.t.label(lang, "btn_write"), f"write:{t['id']}")])
        if t["stage"] != "closed":  # «Problem solved» closes the ticket, also one the team marked resolved
            rows.append([button(c.t.label(lang, "btn_solved"), f"solve:{t['id']}")])
        if t["stage"] in ("resolved", "closed"):
            rows.append([button(c.t.label(lang, "btn_reopen"), f"reopen:{t['id']}")])
        rows.append([button(c.t.label(lang, "btn_all_tickets"), "tickets"), button(c.t.label(lang, "btn_main_menu"), "menu")])
        await self._show(message, text[:4096], InlineKeyboardMarkup(inline_keyboard=rows), edit)

    async def languages(self, message: Message, c: Content, lang: str) -> None:
        buttons = [button(c.language_name(code), f"lang:{code}") for code in c.codes]
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        await message.answer(c.t(lang, "choose_language"), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    async def contacts(self, message: Message, c: Content, lang: str, user_id: int) -> None:
        """«My contacts»: the user decides whether to leave a phone or an e-mail, and can delete them."""
        data = await self.api.post("contacts/get", {"user_id": user_id})
        none = c.t(lang, "contacts_none")
        text = c.t(lang, "contacts_title", phone=escape(data["phone"] or none), email=escape(data["email"] or none))
        rows = [[button(c.t.label(lang, "btn_email"), "contacts:email")]]
        if data["phone"] or data["email"]:
            rows.append([button(c.t.label(lang, "btn_contacts_delete"), "contacts:delete")])
        rows.append([button(c.t.label(lang, "btn_main_menu"), "menu")])
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        # Telegram shares a phone number only through a keyboard button, and only the user's own
        share = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=c.t.label(lang, "btn_share_phone"), request_contact=True)],
                                              [KeyboardButton(text=c.t.label(lang, "btn_main_menu"))]],
                                    resize_keyboard=True, one_time_keyboard=True)
        await message.answer("👇", reply_markup=share)

    async def staff_start(self, message: Message, c: Content, lang: str, state: FSMContext, telegram_id: int, ticket_id: int,
                          mode: str) -> None:
        """A staff member tapped «Reply» or «Note» under a ticket notification: show the ticket, wait for the text."""
        try:
            ticket = await self.api.post("staff/ticket", {"telegram_id": telegram_id, "ticket_id": ticket_id, "mode": mode})
        except ApiError as error:
            return await message.answer(c.t(lang, "staff_denied") if str(error) in STAFF_DENIED else c.t(lang, "temporary"))
        if ticket["closed"] and mode == "reply":
            return await message.answer(c.t(lang, "staff_closed", number=ticket["number"]))
        await state.set_state(StaffReply.note if mode == "note" else StaffReply.reply)
        await state.update_data(ticket_id=ticket["id"])
        cancel = InlineKeyboardMarkup(inline_keyboard=[[button(c.t.label(lang, "btn_cancel"), "cancel")]])
        if mode == "note":
            return await message.answer(c.t(lang, "staff_note_prompt", number=ticket["number"]), reply_markup=cancel)
        history = "\n".join(f"<b>{escape(m['author'] or '')}:</b> {escape(m['text'])}" for m in ticket["messages"])
        await message.answer(c.t(lang, "staff_reply_prompt", number=ticket["number"], user=escape(ticket["user"]),
                                 history=history + "\n" if history else "")[:4096], reply_markup=cancel)

    async def staff_send(self, message: Message, c: Content, lang: str, state: FSMContext | None, ticket_id: int, mode: str) -> None:
        """The staff member's answer or note goes into the ticket — like a dashboard reply, with the same permissions."""
        body = payload(message)
        if body["kind"] == "other" or (body["kind"] == "text" and not (body["text"] or "").strip()):
            return await message.answer(c.t(lang, "unsupported"))
        try:
            result = await self.api.post("staff/message", {"telegram_id": message.from_user.id, "ticket_id": ticket_id, "mode": mode,
                                                           "kind": body["kind"], "text": body["text"], "file_id": body["file_id"],
                                                           "file_name": body["file_name"], "dedupe_key": body["dedupe_key"]})
        except ApiError as error:
            code = str(error)
            if code == "ticket_closed":
                if state is not None:
                    await state.clear()
                number = (await self.api.post("staff/ticket", {"telegram_id": message.from_user.id, "ticket_id": ticket_id}))["number"]
                return await message.answer(c.t(lang, "staff_closed", number=number))
            return await message.answer(c.t(lang, "staff_denied") if code in STAFF_DENIED else c.t(lang, "temporary"))
        if state is not None:
            await state.clear()
        await message.answer(c.t(lang, "staff_note_saved" if mode == "note" else "staff_sent", number=result["number"]))

    async def privacy(self, message: Message, c: Content, lang: str) -> None:
        """What is stored and why, once, at the first /start — when the administrator wrote a notice."""
        text, url = c.name(c.settings.get("privacy_notice") or {}, lang), c.settings.get("privacy_url") or ""
        if not text:
            return
        markup = InlineKeyboardMarkup(inline_keyboard=[[button(c.t.label(lang, "btn_privacy") or "Privacy policy", url=url)]]) if url else None
        await message.answer(c.t.direction(lang, render_text(text)), reply_markup=markup)

    def offline(self, c: Content, lang: str) -> str:
        hours = c.settings.get("business_hours") or {}
        if c.settings.get("offline_auto_reply", True) and not options.is_open(hours):
            return "\n\n" + c.t(lang, "offline_note", hours=options.describe_hours(hours), timezone=hours.get("timezone", "UTC"))
        return ""

    # ------------------------------------------------------------ the safety net
    async def guard(self, event: Message | CallbackQuery, action, state: FSMContext | None = None) -> None:
        """Private chats only; backend error codes become messages in the user's language; never silence."""
        message = event.message if isinstance(event, CallbackQuery) else event
        if message is None or event.from_user is None:
            return
        async with self.lock(event.from_user.id):
            await self._guarded(event, message, action)

    async def _guarded(self, event: Message | CallbackQuery, message: Message, action) -> None:
        c, lang = None, "en"
        try:
            c = await self.cache.get()
            lang = c.resolve(event.from_user.language_code)
            if message.chat.type != "private":
                return  # support conversations are private; nothing is said in groups
            profile = await self.api.post("register", self.identity(event.from_user))
            lang = profile["language"]
            if profile["banned"]:
                await message.answer(c.t(lang, "banned"))
                if isinstance(event, CallbackQuery):
                    await event.answer()
                return
            await action(message, c, lang, profile)
            if isinstance(event, CallbackQuery):
                with contextlib.suppress(Exception):
                    await event.answer()
        except ApiError as error:
            code = str(error)
            text = c.t(lang, code) if c and code in FILE_ERRORS | {"rate_limit"} else (c.t(lang, "temporary") if c else "⚠️")
            if code == "rate_limit" and c:
                text = c.t(lang, "rate_limit")
            if isinstance(event, CallbackQuery):
                await event.answer(text[:200], show_alert=True)
            else:
                await message.answer(text or c.t(lang, "temporary"))
        except Exception:
            log.exception('{"event":"handler_error"}')
            with contextlib.suppress(Exception):
                await self.api.post("error", {"kind": "handler_error", "message": f"update from {event.from_user.id}"})
            with contextlib.suppress(Exception):
                await message.answer(c.t(lang, "temporary") if c else "⚠️ Please try again in a minute.")


class Dedupe(BaseMiddleware):
    """Telegram may deliver an update twice (webhook retries, restarts): the backend remembers handled update ids."""

    def __init__(self, api: ApiClient):
        self.api = api

    async def __call__(self, handler, event: Update, data):
        try:
            if not (await self.api.post("update", {"update_id": event.update_id}))["new"]:
                return None
        except Exception:
            pass  # backend unavailable: handle it anyway, the handlers answer with «try again»
        return await handler(event, data)


def router(ctx: Context) -> Router:
    r = Router(name="support")

    # ------------------------------------------------------------ commands
    @r.message(CommandStart())
    async def start(message: Message, state: FSMContext, command: CommandObject):
        async def action(m, c, lang, profile):
            await state.clear()
            if profile["created"]:
                await ctx.privacy(m, c, lang)
                if c.settings.get("ask_language_first") and len(c.codes) > 1:
                    return await ctx.languages(m, c, lang)
            await ctx.welcome(m, c, lang, message.from_user.first_name or "")
        await ctx.guard(message, action, state)

    @r.message(or_f(Command("menu"), Command("help")))
    async def menu(message: Message, state: FSMContext):
        async def action(m, c, lang, profile):
            await state.clear()
            await ctx.main_menu(m, c, lang)
        await ctx.guard(message, action, state)

    @r.message(Command("faq"))
    async def faq_command(message: Message, state: FSMContext):
        await ctx.guard(message, lambda m, c, lang, p: ctx.faq(m, c, lang, message.from_user.id, None, state=state), state)

    @r.message(Command("support"))
    async def support_command(message: Message, state: FSMContext):
        await ctx.guard(message, lambda m, c, lang, p: ctx.start_support(m, c, lang, state), state)

    @r.message(Command("tickets"))
    async def tickets_command(message: Message, state: FSMContext):
        async def action(m, c, lang, profile):
            await state.clear()
            await ctx.tickets(m, c, lang, message.from_user.id)
        await ctx.guard(message, action, state)

    @r.message(Command("language"))
    async def language_command(message: Message, state: FSMContext):
        await ctx.guard(message, lambda m, c, lang, p: ctx.languages(m, c, lang), state)

    @r.message(or_f(Command("stop"), Command("news")))
    async def announcements(message: Message, command: CommandObject):
        async def action(m, c, lang, profile):
            out = command.command == "stop"
            await ctx.api.post("announcements", {"user_id": message.from_user.id, "out": out})
            await m.answer(c.t(lang, "news_off" if out else "news_on"))
        await ctx.guard(message, action)

    @r.message(Command("contacts"))
    async def contacts_command(message: Message, state: FSMContext):
        async def action(m, c, lang, profile):
            await state.clear()
            await ctx.contacts(m, c, lang, message.from_user.id)
        await ctx.guard(message, action, state)

    @r.message(Command("link"))
    async def link(message: Message, command: CommandObject):
        async def action(m, c, lang, profile):
            try:
                result = await ctx.api.post("link", {"user_id": message.from_user.id, "code": (command.args or "").strip()})
                await m.answer(c.t(lang, "link_ok", name=result["name"]))
            except ApiError:
                await m.answer(c.t(lang, "link_bad"))
        await ctx.guard(message, action)

    # ------------------------------------------------------------ buttons
    @r.callback_query(F.data == "menu")
    async def to_menu(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            await state.clear()
            await ctx.main_menu(m, c, lang, edit=True)
        await ctx.guard(event, action, state)

    @r.callback_query(F.data.startswith("faq:"))
    async def faq_button(event: CallbackQuery, state: FSMContext):
        value = event.data.split(":", 1)[1]
        node_id = None if value == "root" else int(value)
        await ctx.guard(event, lambda m, c, lang, p: ctx.faq(m, c, lang, event.from_user.id, node_id, edit=True, state=state), state)

    @r.callback_query(F.data.startswith("page:"))
    async def page(event: CallbackQuery):
        async def action(m, c, lang, profile):
            item = next((i for i in c.menu if str(i["id"]) == event.data[5:]), None)
            if item:
                calls = render.plan(item["content"], lang, c.default, rtl=c.rtl(lang),
                                    extra_buttons=[[{"text": c.t.label(lang, "btn_main_menu"), "callback_data": "menu"}]])
                await ctx.sender.run(m.chat.id, calls)
        await ctx.guard(event, action)

    @r.callback_query(or_f(F.data == "support", F.data.startswith("support:")))
    async def support(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            node = c.tree.get(int(event.data.split(":")[1])) if ":" in event.data else None
            await ctx.start_support(m, c, lang, state, faq_node=node)
        await ctx.guard(event, action, state)

    @r.callback_query(Support.department, F.data.startswith("dep:"))
    async def department(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            data = {**await state.get_data(), "department_id": int(event.data[4:])}
            await ctx.ask_category(m, c, lang, state, data)
        await ctx.guard(event, action, state)

    @r.callback_query(Support.category, F.data.startswith("cat:"))
    async def category(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            value = int(event.data[4:])
            data = {**await state.get_data(), "category_id": value or None, "skip_category": not value}
            await state.set_state(Support.describe)
            await state.update_data(**data)
            await m.answer(c.t(lang, "support_describe"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [button(c.t.label(lang, "btn_cancel"), "cancel")]]))
        await ctx.guard(event, action, state)

    @r.callback_query(F.data == "cancel")
    async def cancel(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            await state.clear()
            await m.answer(c.t(lang, "support_cancelled"), reply_markup=ctx.menu_markup(c, lang))
        await ctx.guard(event, action, state)

    @r.callback_query(F.data == "tickets")
    async def tickets_button(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            await state.clear()
            await ctx.tickets(m, c, lang, event.from_user.id, edit=True)
        await ctx.guard(event, action, state)

    @r.callback_query(F.data.startswith("t:"))
    async def ticket_button(event: CallbackQuery):
        await ctx.guard(event, lambda m, c, lang, p: ctx.ticket(m, c, lang, event.from_user.id, int(event.data[2:])))

    @r.callback_query(F.data.startswith("write:"))
    async def write(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            ticket = await ctx.api.post(f"tickets/{event.data[6:]}", {"user_id": event.from_user.id})
            await state.set_state(Support.writing)
            await state.update_data(ticket_id=ticket["id"])
            await m.answer(c.t(lang, "ticket_write", number=ticket["number"]), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [button(c.t.label(lang, "btn_cancel"), "cancel")]]))
        await ctx.guard(event, action, state)

    @r.callback_query(F.data.startswith("solve:"))
    async def solve(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            result = await ctx.api.post(f"tickets/{event.data[6:]}/close", {"user_id": event.from_user.id})
            await state.clear()  # the next message no longer goes to this ticket
            with contextlib.suppress(Exception):
                await m.edit_reply_markup(reply_markup=None)  # the buttons under «resolved» are used up
            if not result["done"]:
                return await m.answer(c.t(lang, "ticket_closed_by_user", number=result["number"]))
            markup = None
            if result["ask_rating"]:
                markup = InlineKeyboardMarkup(inline_keyboard=[[button("⭐" * n, f"rate:{result['id']}:{n}") for n in (1, 2, 3)],
                                                               [button("⭐" * n, f"rate:{result['id']}:{n}") for n in (4, 5)]])
            text = c.t(lang, "ticket_closed_by_user", number=result["number"])
            await m.answer(text + ("\n\n" + c.t(lang, "status_resolved", number=result["number"]).split("\n", 1)[-1] if markup else ""),
                           reply_markup=markup)
        await ctx.guard(event, action, state)

    @r.callback_query(F.data.startswith("reopen:"))
    async def reopen(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            result = await ctx.api.post(f"tickets/{event.data[7:]}/reopen", {"user_id": event.from_user.id})
            if result["result"] == "ok":
                await state.set_state(Support.writing)
                await state.update_data(ticket_id=result["id"])
                await m.answer(c.t(lang, "ticket_reopened", number=result["number"]))
            else:
                await m.answer(c.t(lang, "reopen_expired" if result["result"] == "expired" else "reopen_not_allowed"),
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button(c.t.label(lang, "btn_contact"), "support")]]))
        await ctx.guard(event, action, state)

    @r.callback_query(F.data.startswith("rate:"))
    async def rate(event: CallbackQuery):
        async def action(m, c, lang, profile):
            _, ticket_id, stars = event.data.split(":")
            await ctx.api.post(f"tickets/{ticket_id}/rate", {"user_id": event.from_user.id, "stars": int(stars)})
            with contextlib.suppress(Exception):
                await m.edit_reply_markup(reply_markup=None)
            await m.answer(c.t(lang, "rate_thanks"), reply_markup=ctx.menu_markup(c, lang))
        await ctx.guard(event, action)

    @r.callback_query(F.data == "contacts")
    async def contacts_button(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            await state.clear()
            await ctx.contacts(m, c, lang, event.from_user.id)
        await ctx.guard(event, action, state)

    @r.callback_query(F.data == "contacts:email")
    async def contacts_email(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            await state.set_state(ContactDetails.email)
            await m.answer(c.t(lang, "contacts_email_prompt"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [button(c.t.label(lang, "btn_cancel"), "cancel")]]))
        await ctx.guard(event, action, state)

    @r.callback_query(F.data == "contacts:delete")
    async def contacts_delete(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            await state.clear()
            await ctx.api.post("contacts", {"user_id": event.from_user.id, "clear": True})
            with contextlib.suppress(Exception):
                await m.edit_reply_markup(reply_markup=None)
            await m.answer(c.t(lang, "contacts_deleted"), reply_markup=ctx.keyboard(c, lang))
        await ctx.guard(event, action, state)

    @r.callback_query(or_f(F.data.startswith("sr:"), F.data.startswith("sn:")))
    async def staff_button(event: CallbackQuery, state: FSMContext):
        """«Reply» / «Note» under a staff notification."""
        async def action(m, c, lang, profile):
            await ctx.staff_start(m, c, lang, state, event.from_user.id, int(event.data[3:]), "note" if event.data[:2] == "sn" else "reply")
        await ctx.guard(event, action, state)

    @r.callback_query(F.data == "lang")
    async def language_button(event: CallbackQuery):
        await ctx.guard(event, lambda m, c, lang, p: ctx.languages(m, c, lang))

    @r.callback_query(F.data.startswith("lang:"))
    async def set_language(event: CallbackQuery, state: FSMContext):
        async def action(m, c, lang, profile):
            chosen = (await ctx.api.post("language", {"user_id": event.from_user.id, "language": event.data[5:]}))["language"]
            with contextlib.suppress(Exception):
                await m.edit_text(c.t(chosen, "language_saved", language=c.language_name(chosen)))
            await ctx.welcome(m, c, chosen, event.from_user.first_name or "")
        await ctx.guard(event, action, state)

    # ------------------------------------------------------------ messages
    @r.message(Support.describe)
    async def describe(message: Message, state: FSMContext):
        async def action(m, c, lang, profile):
            item = ctx.menu_item(c, message.text)
            if item:  # a menu button on the keyboard leaves the steps
                return await ctx.open_item(item, m, c, lang, state, message.from_user.id)
            body = payload(message)
            if body["kind"] == "other" or (body["kind"] == "text" and not (body["text"] or "").strip()):
                return await m.answer(c.t(lang, "unsupported"))
            data = await state.get_data()
            ticket = await ctx.api.post("tickets", {"user_id": message.from_user.id, **body, "department_id": data.get("department_id"),
                                                    "category_id": data.get("category_id"), "faq_node_id": data.get("faq_node_id")})
            await state.set_state(Support.writing)
            await state.update_data(ticket_id=ticket["id"])
            await m.answer(c.t(lang, "ticket_created", number=ticket["number"], status=ticket["status_name"]) + ctx.offline(c, lang),
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button(c.t.label(lang, "btn_main_menu"), "menu")]]))
        await ctx.guard(message, action, state)

    @r.message(or_f(StaffReply.reply, StaffReply.note))
    async def staff_writes(message: Message, state: FSMContext):
        async def action(m, c, lang, profile):
            item = ctx.menu_item(c, message.text)
            if item:
                return await ctx.open_item(item, m, c, lang, state, message.from_user.id)
            mode = "note" if await state.get_state() == StaffReply.note.state else "reply"
            await ctx.staff_send(m, c, lang, state, (await state.get_data())["ticket_id"], mode)
        await ctx.guard(message, action, state)

    @r.message(F.contact)
    async def shared_contact(message: Message, state: FSMContext):
        """The user shared a phone number with the keyboard button: only their own is kept."""
        async def action(m, c, lang, profile):
            await state.clear()
            if message.contact.user_id != message.from_user.id:
                return await m.answer(c.t(lang, "contacts_not_own"))
            try:
                await ctx.api.post("contacts", {"user_id": message.from_user.id, "phone": message.contact.phone_number})
            except ApiError:
                return await m.answer(c.t(lang, "contacts_bad_phone"), reply_markup=ctx.keyboard(c, lang))
            await m.answer(c.t(lang, "contacts_saved"), reply_markup=ctx.keyboard(c, lang))
        await ctx.guard(message, action, state)

    @r.message(ContactDetails.email)
    async def typed_email(message: Message, state: FSMContext):
        async def action(m, c, lang, profile):
            item = ctx.menu_item(c, message.text)
            if item:
                return await ctx.open_item(item, m, c, lang, state, message.from_user.id)
            try:
                await ctx.api.post("contacts", {"user_id": message.from_user.id, "email": (message.text or "").strip()[:255]})
            except ApiError:
                return await m.answer(c.t(lang, "contacts_bad_email"))  # stays in the step: the user tries again
            await state.clear()
            await m.answer(c.t(lang, "contacts_saved"), reply_markup=ctx.keyboard(c, lang))
        await ctx.guard(message, action, state)

    @r.message(F.chat.type == "private")
    async def anything(message: Message, state: FSMContext):
        """A message outside the steps: a staff member's swipe-reply to a ticket notification, else into the ticket being
        written to, else the user's open ticket, else the FAQ search."""
        async def action(m, c, lang, profile):
            item = ctx.menu_item(c, message.text)
            if item:
                return await ctx.open_item(item, m, c, lang, state, message.from_user.id)
            notification = staff_notification(message.reply_to_message)
            if notification:
                return await ctx.staff_send(m, c, lang, None, *notification)
            body = payload(message)
            if body["kind"] == "other":
                return await m.answer(c.t(lang, "unsupported"))
            data = await state.get_data() if await state.get_state() == Support.writing.state else {}
            ticket_id = data.get("ticket_id") or (profile["open_ticket"] or {}).get("id")
            if ticket_id:
                try:
                    result = await ctx.api.post(f"tickets/{ticket_id}/message", {"user_id": message.from_user.id, **body})
                    try:  # a quiet sign that the message reached the ticket
                        await m.react([ReactionTypeEmoji(emoji="✍")])
                    except Exception:
                        await m.answer(c.t(lang, "ticket_added", number=result["number"]))
                    return
                except ApiError as error:
                    if str(error) not in ("ticket_closed", "not_found"):
                        raise
                    await state.clear()
            text = (body["text"] or "").strip()
            if body["kind"] != "text" or wants_person(text):
                return await ctx.start_support(m, c, lang, state)
            found = c.tree.search(text, lang, c.default) if c.settings.get("search_enabled", True) else []
            await ctx.api.post("event", {"user_id": message.from_user.id, "kind": "search" if found else "search_miss",
                                         "data": {"query": text[:200], "results": [n["id"] for n in found]}})
            rows = [[button(c.name(n["title"], lang), f"faq:{n['id']}")] for n in found]
            rows += [[button(c.t.label(lang, "btn_contact"), "support")], [button(c.t.label(lang, "btn_main_menu"), "menu")]]
            await m.answer(c.t(lang, "search_found" if found else "search_miss"), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await ctx.guard(message, action, state)

    @r.my_chat_member()
    async def membership(event: ChatMemberUpdated):
        """The user blocked (or unblocked) the bot: announcements stop, the profile shows it."""
        if event.chat.type == "private" and event.new_chat_member.status == "kicked":
            with contextlib.suppress(Exception):
                await ctx.api.post("blocked", {"user_id": event.from_user.id})

    return r


def dispatcher(ctx: Context, storage) -> Dispatcher:
    dp = Dispatcher(storage=storage)
    dp.update.outer_middleware(Dedupe(ctx.api))
    dp.include_router(router(ctx))
    return dp
