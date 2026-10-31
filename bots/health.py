"""Docker healthcheck: `python -m bots.health` exits 0 if the bot sent a heartbeat in the last 2 minutes."""
from datetime import timedelta
from backend.database import Session
from backend.models import BotState, aware, now

with Session() as db:
    state = db.get(BotState, "support")
    healthy = bool(state and state.last_seen and aware(state.last_seen) > now() - timedelta(seconds=120))
    raise SystemExit(0 if healthy else 1)
