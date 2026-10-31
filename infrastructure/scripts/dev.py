"""Local all-in-one run for development and live bot testing without Docker.

    python -m infrastructure.scripts.dev

Runs the API (http://127.0.0.1:8100), the background tasks in a thread pool, the scheduler and the bot (when
BOT_TOKEN is in local/.env). Uses SQLite, an in-process Redis replacement and disk storage in local/. Production uses
PostgreSQL, Redis and Celery via docker-compose.yml.
"""
import asyncio
import logging
import os
import secrets
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / "local"
LOCAL.mkdir(exist_ok=True)


def free_port(start: int) -> int:
    """The first free local port from `start` (another application may already use it)."""
    import socket
    for port in range(start, start + 50):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("No free local port found")


PORT = int(os.environ.get("DEV_PORT") or free_port(8100))  # the media platform's local runner uses 8000

env_file = LOCAL / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

# Generated once and kept, so dashboard sessions survive restarts of the local runner.
secrets_file = LOCAL / "dev-secrets.env"
if not secrets_file.exists():
    secrets_file.write_text("".join(f"{key}={secrets.token_hex(32)}\n" for key in ("JWT_SECRET", "BOT_SERVICE_KEY")), encoding="utf-8")
for line in secrets_file.read_text(encoding="utf-8").splitlines():
    if "=" in line:
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)

defaults = {
    "DATABASE_URL": f"sqlite:///{(LOCAL / 'dev.db').as_posix()}", "REDIS_URL": "redis://fake",
    "ADMIN_ORIGIN": "http://localhost:5173", "COOKIE_SECURE": "false", "API_INTERNAL_URL": f"http://127.0.0.1:{PORT}",
    "STORAGE_DIR": str(LOCAL / "storage"), "BACKUP_DIR": str(LOCAL / "backups"), "ENVIRONMENT": "development",
    "WEBHOOK_BASE_URL": "",
}
for key, value in defaults.items():
    os.environ.setdefault(key, value)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # its lines contain Telegram URLs with the token
log = logging.getLogger("dev")


def patch_infrastructure():
    """Redis → fakeredis; Celery → a thread pool running the same task functions, with retries."""
    import fakeredis
    from backend.services import limits, queue
    from workers import tasks
    from workers.telegram import TelegramRetry

    redis = fakeredis.FakeRedis(decode_responses=True)
    limits.redis_client.cache_clear()
    limits.redis_client = lambda: redis
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="worker")
    functions = {"workers.tasks.send_message": tasks.deliver, "workers.tasks.notify_staff": tasks.notify,
                 "workers.tasks.copy_attachment": tasks.copy, "workers.tasks.send_campaign": tasks.run_campaign,
                 "workers.tasks.send_preview": tasks.preview, "workers.tasks.reconcile": tasks.reconcile_now}

    def run(name: str, *args, countdown: float = 0, attempt: int = 1):
        def job():
            try:
                functions[name](*args)
            except TelegramRetry as error:
                if attempt < 5:
                    delay = error.retry_after or 10 * attempt
                    log.info("%s: Telegram busy, retry in %ss", name, delay)
                    threading.Timer(delay, lambda: run(name, *args, attempt=attempt + 1)).start()
            except Exception:
                log.exception("task %s crashed", name)
        if name not in functions:
            return
        if countdown:
            threading.Timer(countdown, lambda: pool.submit(job)).start()
        else:
            pool.submit(job)

    queue.task = run

    def beat():
        from backend.database import Session
        from backend.services import campaigns, digest, tickets
        while True:
            time.sleep(60)
            try:
                tasks.reconcile_now()
                with Session() as db:
                    tickets.scheduled(db)
                    digest.weekly(db)
                    campaigns.start_due(db)
                    from backend.services import options
                    from backend.models import now
                    options.put(db, "scheduler_seen", now().isoformat())
                    db.commit()
            except Exception:
                log.exception("scheduler")
    threading.Thread(target=beat, daemon=True, name="beat").start()


def prepare_database():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import func, select
    from backend.database import Session
    from backend.models import Staff
    from backend.services import setup
    from backend.services.auth import hasher

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "database" / "migrations"))
    command.upgrade(config, "head")
    credentials = LOCAL / "dev-admin.txt"
    with Session() as db:
        setup.seed(db)
        if not db.scalar(select(func.count()).select_from(Staff)):
            password = secrets.token_urlsafe(12)
            db.add(Staff(username="admin", display_name="Administrator", password_hash=hasher.hash(password), role_id="super_admin"))
            db.commit()
            credentials.write_text(f"username: admin\npassword: {password}\n", encoding="utf-8")
    log.info("Dashboard sign-in for the local run: %s", credentials)
    if os.environ.get("DEMO_DATA") == "1":  # synthetic, clearly marked data for presentations (scripts/demo_data.py)
        from infrastructure.scripts import demo_data
        with Session() as db:
            if not demo_data.has_demo(db):
                log.info("Demo data: %s tickets created", demo_data.fill(db))


def start_api():
    (LOCAL / "dev-port.txt").write_text(str(PORT), encoding="utf-8")  # the dashboard's dev server proxies /api here
    import uvicorn
    from backend.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="api").start()
    for _ in range(100):
        if server.started:
            return
        time.sleep(0.1)
    raise SystemExit(f"The API did not start on port {PORT}; set DEV_PORT in local/.env to a free port")


async def run_bot():
    from backend.config import settings
    from bots.runner import serve
    if not settings().bot_token.get_secret_value():
        log.warning("No BOT_TOKEN in local/.env - API only.")
        while True:
            await asyncio.sleep(3600)
    await serve()


def main():
    patch_infrastructure()
    prepare_database()
    start_api()
    log.info("API: http://127.0.0.1:%s/api/docs  |  dashboard: cd dashboard && npm run dev -> http://localhost:5173", PORT)
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
