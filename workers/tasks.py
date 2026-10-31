"""Background tasks: delivery of replies, direct and system messages, staff notifications in Telegram,
copies of users' attachments, announcements, previews and the scheduled checks (SLA, auto-close, retention).

Every task is safe to run twice: the database row says what was already done (a message «sent», a delivery «sent»),
and rows are marked «sending» before the Telegram call. The functions below take an optional Telegram client, so
the tests run them without the network.
"""
import html
import logging
import time
from datetime import timedelta
from celery import shared_task
from sqlalchemy import delete, func, or_, select
from backend.config import settings
from backend.database import Session
from backend.models import (Campaign, CampaignDelivery, ErrorLog, FaqNode, MediaAsset, Message, ProcessedUpdate, StaffNotification,
                            Ticket, TgUser, now)
from backend.services import audit, campaigns, content, faq, limits, media, options, queue, render, tickets, users
from backend.services import notify as notify_service
from backend.services.i18n import pick
from workers.telegram import Telegram, TelegramError, TelegramRetry

log = logging.getLogger("workers")
MAX_ATTEMPTS = 6
STUCK = timedelta(minutes=10)


def assets(db):
    """Asset source for Telegram.execute: the file on disk, Telegram's cached id and how to remember a new one."""
    def source(asset_id: int):
        asset = db.get(MediaAsset, asset_id)
        if asset is None:
            raise TelegramError("media file missing")

        def remember(file_id):
            if file_id:
                asset.tg_file_id = file_id
        return media.path_of(asset), asset.tg_file_id, remember
    return source


# ---------------------------------------------------------------- messages to users
def build(db, message: Message) -> list[dict]:
    """The Telegram calls of one outgoing message, in the user's language."""
    t = content.translator(db)
    user = db.get(TgUser, message.user_id)
    ticket = db.get(Ticket, message.ticket_id) if message.ticket_id else None
    lang = ticket.language if ticket else users.language(db, user)
    body = html.escape(message.text or "", quote=False)
    if message.direction == "system":
        text = message.text or ""  # built from translated texts already
    elif ticket:
        text = t(lang, "staff_reply", author=message.author, number=ticket.number, text="") + body
    else:
        text = t(lang, "direct_message", company=options.get(db, "company_name"), text="") + body
    markup = None
    solved = [{"text": t.label(lang, "btn_solved"), "callback_data": f"solve:{ticket.id}"}] if ticket else []
    help_ = [{"text": t.label(lang, "btn_still_need_help"), "callback_data": f"reopen:{ticket.id}"}] if ticket else []
    if message.markup == "rating" and ticket:
        markup = {"inline_keyboard": [[{"text": "⭐" * n, "callback_data": f"rate:{ticket.id}:{n}"} for n in (1, 2, 3)],
                                      [{"text": "⭐" * n, "callback_data": f"rate:{ticket.id}:{n}"} for n in (4, 5)], solved, help_]}
    elif message.markup == "reopen" and ticket:
        markup = {"inline_keyboard": [solved, help_]}
    elif ticket and message.direction == "out":
        markup = {"inline_keyboard": [[{"text": t.label(lang, "btn_write"), "callback_data": f"write:{ticket.id}"}]]}
    return render.message_plan(message.kind, text, message.asset_id, markup, file_id=message.file_id if message.direction == "out" else None)


def deliver(message_id: int, telegram: Telegram | None = None) -> str:
    """Sends one stored message; returns its delivery state. Raises TelegramRetry for a later attempt."""
    with Session() as db:
        message = db.get(Message, message_id)
        if message is None or message.delivery in ("sent", "blocked", "failed", ""):
            return message.delivery if message else "missing"
        user = db.get(TgUser, message.user_id)
        if user.bot_blocked or user.anonymized:
            message.delivery, message.error = "blocked", "bot_blocked" if user.bot_blocked else "anonymized"
            db.commit()
            return message.delivery
        message.delivery, message.attempts = "sending", message.attempts + 1
        db.commit()
        client = telegram or Telegram()
        try:
            results = client.execute(user.id, build(db, message), assets(db))
            message.delivery, message.sent_at, message.error = "sent", now(), ""
            last = results[-1] if results else {}
            message.tg_message_id = (last[0] if isinstance(last, list) else last).get("message_id")
        except TelegramRetry:
            message.delivery = "queued" if message.attempts < MAX_ATTEMPTS else "failed"
            message.error = "telegram_unavailable"
            if message.delivery == "failed":
                audit.error(db, "delivery", "telegram_unavailable", f"message {message.id}")
            db.commit()
            if message.delivery == "queued":
                raise
            return message.delivery
        except TelegramError as error:
            message.delivery, message.error = ("blocked" if error.blocked else "failed"), error.description[:255]
            if error.blocked:
                user.bot_blocked, user.bot_blocked_at = True, now()
            else:
                audit.error(db, "delivery", "telegram_error", f"message {message.id}: {error.description}")
        finally:
            if telegram is None:
                client.close()
        db.commit()
        return message.delivery


@shared_task(name="workers.tasks.send_message", bind=True, max_retries=MAX_ATTEMPTS)
def send_message(self, message_id: int):
    try:
        deliver(message_id)
    except TelegramRetry as error:
        raise self.retry(countdown=error.retry_after or min(10 * 3 ** self.request.retries, 600)) from None


def staff_buttons(chat_id: int, ticket_id: int | None) -> dict | None:
    """Under a ticket notification: answer the user or leave a note right from Telegram (the bot routes sr:/sn:), and
    open the ticket in the dashboard. Labels follow the language the staff member uses in the bot."""
    if not ticket_id:
        return None
    with Session() as db:
        t = content.translator(db)
        person = db.get(TgUser, chat_id)
        lang = users.language(db, person) if person else t.default
        rows = [[{"text": t.label(lang, "btn_staff_reply"), "callback_data": f"sr:{ticket_id}"},
                 {"text": t.label(lang, "btn_staff_note"), "callback_data": f"sn:{ticket_id}"}]]
    origin = settings().admin_origin.rstrip("/")
    if origin.startswith("https://"):  # Telegram refuses buttons to plain-http and local addresses
        rows.append([{"text": t.label(lang, "btn_open_dashboard"), "url": f"{origin}/?ticket={ticket_id}"}])
    return {"inline_keyboard": rows}


def staff_text(chat_id: int, text: str, kind: str | None, params: dict | None) -> str:
    """The notification in the language the staff member uses in the bot (English text for old queued tasks)."""
    if not kind:
        return text
    with Session() as db:
        t = content.translator(db)
        person = db.get(TgUser, chat_id)
        return notify_service.render(t, users.language(db, person) if person else t.default, kind, params or {}) or text


def notify(chat_id: int, text: str, ticket_id: int | None, telegram: Telegram | None = None, kind: str | None = None,
           params: dict | None = None) -> None:
    client = telegram or Telegram()
    try:
        client.call("sendMessage", {"chat_id": chat_id, "text": html.escape(staff_text(chat_id, text, kind, params), quote=False)[:4096],
                                    "link_preview_options": {"is_disabled": True}, "reply_markup": staff_buttons(chat_id, ticket_id)})
    finally:
        if telegram is None:
            client.close()


@shared_task(name="workers.tasks.notify_staff", bind=True, max_retries=3)
def notify_staff(self, chat_id: int, text: str, ticket_id: int | None = None, kind: str | None = None, params: dict | None = None):
    try:
        notify(chat_id, text, ticket_id, kind=kind, params=params)
    except TelegramRetry as error:
        raise self.retry(countdown=error.retry_after or 30) from None
    except TelegramError:
        log.info('{"event":"staff_notification_refused"}')


def copy(message_id: int, telegram: Telegram | None = None) -> bool:
    """A copy of a user's attachment in our storage: Telegram file links expire, the ticket history must not."""
    with Session() as db:
        message = db.get(Message, message_id)
        if message is None or not message.file_id:
            return False
        target = media.attachment_path(message.id, message.file_name)
        if target.exists():
            return True
        client = telegram or Telegram()
        try:
            info = client.file_info(message.file_id)
            if (info.get("file_size") or 0) > settings().telegram_receive_bytes or not info.get("file_path"):
                return False
            size = client.download(info["file_path"], target, settings().telegram_receive_bytes)
            message.file_size = message.file_size or size
            db.commit()
            return True
        except TelegramError as error:
            audit.error(db, "worker", "attachment_copy", f"message {message.id}: {error.description}")
            db.commit()
            return False
        finally:
            if telegram is None:
                client.close()


@shared_task(name="workers.tasks.copy_attachment", bind=True, max_retries=5)
def copy_attachment(self, message_id: int):
    try:
        copy(message_id)
    except TelegramRetry as error:
        raise self.retry(countdown=error.retry_after or 60) from None


# ---------------------------------------------------------------- announcements
def campaign_plan(db, campaign: Campaign, lang: str, t) -> list[dict]:
    rtl = lang in t.rtl
    return render.plan(campaign.content, lang, t.default, rtl=rtl, buttons_text=t.label(lang, "btn_main_menu") or "⬇️")


def run_campaign(campaign_id: int, telegram: Telegram | None = None, budget_seconds: float = 300, sleep=time.sleep) -> str:
    """Sends pending deliveries at the campaign's rate until done, paused, cancelled or the time budget is used up
    (then the task starts itself again). One sender per campaign (a Redis lock)."""
    lock = limits.redis_client().set(f"campaign:{campaign_id}:lock", "1", nx=True, ex=int(budget_seconds) + 120)
    if not lock:
        return "busy"
    client = telegram or Telegram()
    started = time.monotonic()
    try:
        with Session() as db:
            t = content.translator(db)
            plans: dict[str, list[dict]] = {}
            while True:
                campaign = db.get(Campaign, campaign_id)
                db.refresh(campaign)
                if campaign is None or campaign.status != "running":
                    return campaign.status if campaign else "missing"
                if time.monotonic() - started > budget_seconds:
                    queue.task("workers.tasks.send_campaign", campaign_id)
                    return "continued"
                moment = now()
                batch = db.scalars(select(CampaignDelivery).where(
                    CampaignDelivery.campaign_id == campaign_id, CampaignDelivery.status == "pending",
                    or_(CampaignDelivery.next_attempt_at.is_(None), CampaignDelivery.next_attempt_at <= moment))
                    .order_by(CampaignDelivery.id).limit(max(campaign.rate_per_second, 1))).all()
                if not batch:
                    waiting = db.scalar(select(func.min(CampaignDelivery.next_attempt_at)).where(
                        CampaignDelivery.campaign_id == campaign_id, CampaignDelivery.status == "pending"))
                    if waiting:
                        queue.task("workers.tasks.send_campaign", campaign_id, countdown=max(5.0, (waiting - moment).total_seconds()))
                        return "waiting"
                    campaigns.finish_if_done(db, campaign)
                    db.commit()
                    return campaign.status
                second = time.monotonic()
                for delivery in batch:
                    delivery.status, delivery.attempts = "sending", delivery.attempts + 1
                    db.commit()  # marked before the call: a restart never sends it twice
                    user = db.get(TgUser, delivery.user_id)
                    if user is None or user.bot_blocked or user.banned or user.campaigns_opt_out or user.anonymized:
                        delivery.status, delivery.error = "skipped", "not_eligible"
                        db.commit()
                        continue
                    if delivery.language not in plans:
                        plans[delivery.language] = campaign_plan(db, campaign, delivery.language, t)
                    try:
                        client.execute(user.id, plans[delivery.language], assets(db))
                        delivery.status, delivery.sent_at, delivery.error = "sent", now(), ""
                    except TelegramRetry as error:
                        if error.retry_after:  # flood control: Telegram asks the whole bot to slow down
                            delivery.status, delivery.attempts = "pending", delivery.attempts - 1
                            db.commit()
                            sleep(min(error.retry_after, 60))
                            continue
                        if delivery.attempts >= campaign.max_attempts:
                            delivery.status, delivery.error = "failed", "telegram_unavailable"
                        else:
                            delivery.status, delivery.next_attempt_at = "pending", now() + timedelta(seconds=30 * delivery.attempts)
                    except TelegramError as error:
                        delivery.status, delivery.error = ("blocked" if error.blocked else "failed"), error.description[:255]
                        if error.blocked:
                            user.bot_blocked, user.bot_blocked_at = True, now()
                    db.commit()
                elapsed = time.monotonic() - second
                if elapsed < 1:
                    sleep(1 - elapsed)  # at most rate_per_second messages each second
    finally:
        limits.redis_client().delete(f"campaign:{campaign_id}:lock")
        if telegram is None:
            client.close()


@shared_task(name="workers.tasks.send_campaign")
def send_campaign(campaign_id: int):
    run_campaign(campaign_id)


@shared_task(name="workers.tasks.start_scheduled_campaigns")
def start_scheduled_campaigns():
    with Session() as db:
        campaigns.start_due(db)
        db.commit()


# ---------------------------------------------------------------- previews
def preview(chat_id: int, rich_content: dict | None, node_id: int | None, lang: str, telegram: Telegram | None = None) -> None:
    """Sends content (or a draft FAQ question with its sub-questions) to a staff member's own Telegram."""
    with Session() as db:
        t = content.translator(db)
        extra = []
        if node_id is not None:
            node = db.get(FaqNode, node_id)
            rich_content = node.content if node else {}
            children = db.scalars(select(FaqNode).where(FaqNode.parent_id == node_id).order_by(FaqNode.position)).all()
            extra = [[{"text": pick(c.title, lang, t.default) or "?", "callback_data": "menu"}] for c in children]
            if not (rich_content or {}).get("blocks"):
                rich_content = {"blocks": [{"type": "text", "text": {lang: f"<b>{html.escape(pick(node.title, lang, t.default))}</b>"}}]} if node else {}
        calls = render.plan(rich_content, lang, t.default, rtl=lang in t.rtl, extra_buttons=extra, empty_text="(empty)",
                            buttons_text=t.label(lang, "faq_choose") or "⬇️")
        client = telegram or Telegram()
        try:
            client.call("sendMessage", {"chat_id": chat_id, "text": "👁 Preview"})
            client.execute(chat_id, calls, assets(db))
            db.commit()
        finally:
            if telegram is None:
                client.close()


@shared_task(name="workers.tasks.send_preview")
def send_preview(chat_id: int, rich_content: dict | None, node_id: int | None, lang: str):
    try:
        preview(chat_id, rich_content, node_id, lang)
    except (TelegramError, TelegramRetry):
        log.info('{"event":"preview_failed"}')


# ---------------------------------------------------------------- scheduled checks
def reconcile_now() -> dict:
    """Finds work the queue lost (broker restart, crashed worker) and starts it again."""
    moment = now()
    with Session() as db:
        lost = db.scalars(select(Message.id).where(or_(
            (Message.delivery == "queued") & (Message.created_at < moment - timedelta(seconds=60)),
            (Message.delivery == "sending") & (Message.created_at < moment - STUCK)), Message.attempts < MAX_ATTEMPTS).limit(500)).all()
        for message_id in lost:
            db.get(Message, message_id).delivery = "queued"
        stuck = db.execute(select(CampaignDelivery).join(Campaign, Campaign.id == CampaignDelivery.campaign_id).where(
            CampaignDelivery.status == "sending", Campaign.status == "running")).scalars().all()
        for delivery in stuck:  # interrupted mid-call: sent or not is unknown; never send twice, report it
            if not limits.redis_client().exists(f"campaign:{delivery.campaign_id}:lock"):
                delivery.status, delivery.error = "failed", "interrupted"
        running = db.scalars(select(Campaign.id).where(Campaign.status == "running")).all()
        db.commit()
    for message_id in lost:
        queue.task("workers.tasks.send_message", message_id)
    for campaign_id in running:
        if not limits.redis_client().exists(f"campaign:{campaign_id}:lock"):
            queue.task("workers.tasks.send_campaign", campaign_id)
    return {"messages": len(lost), "campaigns": len(running)}


@shared_task(name="workers.tasks.reconcile")
def reconcile():
    reconcile_now()


@shared_task(name="workers.tasks.check_tickets")
def check_tickets():
    """Every minute: SLA breaches and early warnings, reminders to users, auto-close; the weekly summary on Mondays."""
    from backend.services import digest
    with Session() as db:
        tickets.scheduled(db)
        digest.weekly(db)
        options.put(db, "scheduler_seen", now().isoformat())  # beat sent it and a worker ran it: both are alive
        db.commit()


@shared_task(name="workers.tasks.retention")
def retention():
    with Session() as db:
        users.apply_retention(db)
        db.execute(delete(ProcessedUpdate).where(ProcessedUpdate.created_at < now() - timedelta(days=7)))
        logs = int(options.get(db, "retention_logs_days") or 0)
        if logs:  # the error log and the bell do not grow for ever; the audit log is kept (accountability)
            cutoff = now() - timedelta(days=logs)
            db.execute(delete(ErrorLog).where(ErrorLog.created_at < cutoff))
            db.execute(delete(StaffNotification).where(StaffNotification.created_at < cutoff))
        days = int(options.get(db, "retention_closed_tickets_days") or 0)
        if days:
            closed = tickets.keys_of(db, {"closed"})
            old = db.scalars(select(Message).join(Ticket, Ticket.id == Message.ticket_id).where(
                Ticket.status.in_(closed), Ticket.closed_at < now() - timedelta(days=days), Message.file_id.is_not(None))).all()
            for message in old:
                media.attachment_path(message.id, message.file_name).unlink(missing_ok=True)
        db.commit()


__all__ = ["deliver", "copy", "run_campaign", "preview", "reconcile_now", "faq"]
