"""Starts the support bot: long polling, or a webhook when WEBHOOK_BASE_URL is set."""
import asyncio
import contextlib
import logging
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand
from backend import observability
from backend.config import settings
from bots.client import ApiClient
from bots.support import Context, dispatcher

log = logging.getLogger("bot")
COMMANDS = ["start", "faq", "support", "tickets", "language", "contacts"]
WEBHOOK_PATH = "/telegram/webhook"
ALLOWED = ["message", "callback_query", "my_chat_member"]


async def heartbeat(ctx: Context, mode: str):
    """Tells the backend the bot is alive (System page, Docker healthcheck) and refreshes the cached content."""
    while True:
        try:
            me = await ctx.bot.get_me()
            await ctx.api.post("heartbeat", {"username": me.username or "", "mode": mode})
            await ctx.cache.get()
        except Exception:
            log.warning('{"event":"heartbeat_failed"}')
        await asyncio.sleep(30)


async def describe(ctx: Context):
    """Menu commands and descriptions in every enabled language, from the bot texts edited in the dashboard."""
    c = await ctx.cache.get()
    for code in [None, *c.codes]:
        lang = code or c.default
        with contextlib.suppress(Exception):
            await ctx.bot.set_my_commands([BotCommand(command=name, description=(c.t.label(lang, f"cmd_{name}") or name)[:256])
                                           for name in COMMANDS], language_code=code)
            about = c.t(lang, "about", company=c.settings.get("company_name", ""))
            await ctx.bot.set_my_short_description(about[:120], language_code=code)


async def serve():
    token = settings().bot_token.get_secret_value()
    if not token:
        raise SystemExit("Set BOT_TOKEN in .env (the token from @BotFather)")
    bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    ctx = Context(bot, ApiClient())
    if settings().redis_url == "redis://fake":  # local run without Redis (infrastructure/scripts/dev.py)
        from aiogram.fsm.storage.memory import MemoryStorage
        storage = MemoryStorage()
    else:
        storage = RedisStorage.from_url(settings().redis_url)  # the dialogue survives restarts
    dp = dispatcher(ctx, storage)
    for _ in range(30):  # the API may still be starting
        try:
            await ctx.cache.get()
            break
        except Exception:
            await asyncio.sleep(2)
    await describe(ctx)
    mode = "webhook" if settings().webhook_base_url else "polling"
    pulse = asyncio.create_task(heartbeat(ctx, mode))
    try:
        if mode == "webhook":
            await webhook(bot, dp)
        else:
            await bot.delete_webhook(drop_pending_updates=False)  # a token used by webhook before
            await dp.start_polling(bot, allowed_updates=ALLOWED)
    finally:
        pulse.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pulse
        await ctx.api.close()
        await bot.session.close()


def webhook_app(bot: Bot, dp, secret: str):
    """The webhook endpoint: Telegram sends X-Telegram-Bot-Api-Secret-Token with every update; any call without the
    right secret is refused before an update is parsed."""
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    if len(secret) < 16:
        raise SystemExit("Set WEBHOOK_SECRET (at least 16 characters)")
    app = web.Application(client_max_size=2_000_000)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    return app


async def webhook(bot: Bot, dp):
    from aiohttp import web
    secret = settings().webhook_secret.get_secret_value()
    runner = web.AppRunner(webhook_app(bot, dp, secret))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", settings().webhook_port).start()
    await bot.set_webhook(settings().webhook_base_url + WEBHOOK_PATH, secret_token=secret, allowed_updates=ALLOWED,
                          drop_pending_updates=False)
    log.info('{"event":"webhook_ready"}')
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main():
    observability.setup_logging("bot")
    observability.setup("bot")  # error tracking when SENTRY_DSN is set; quiet HTTP logs (they would contain the token)
    asyncio.run(serve())
