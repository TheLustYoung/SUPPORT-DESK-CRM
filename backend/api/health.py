from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from backend.database import session
from backend.services import media
from backend.services.limits import redis_client

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live():
    return {"status": "ok"}


@router.get("/ready")
def ready(db=Depends(session)):
    def storage():
        probe = media.root() / ".ready"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()

    checks = {}
    for name, probe in (("database", lambda: db.execute(text("SELECT 1"))), ("redis", lambda: redis_client().ping()), ("storage", storage)):
        try:
            probe()
            checks[name] = "ok"
        except Exception:
            checks[name] = "unavailable"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if healthy else "unavailable", **checks}, status_code=200 if healthy else 503)
