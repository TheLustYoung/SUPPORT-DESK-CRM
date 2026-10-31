"""Audit log and the System page: bot status, health of the dependencies, delivery queue,
recent errors and backups with their restore checks."""
import json
from datetime import timedelta
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select, text
from backend.api.deps import staff_required
from backend.config import settings
from backend.database import session
from backend.models import AuditLog, BotState, CampaignDelivery, ErrorLog, Message, aware, now
from backend.services import audit, options, queue
from backend.services.limits import redis_client

router = APIRouter(prefix="/api/admin", tags=["system"])


@router.get("/audit")
def audit_log(action: str = "", staff_name: str = "", target: str = "", date_from: str = "", page: int = Query(1, ge=1),
              staff=Depends(staff_required), db=Depends(session)):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if staff_name:
        stmt = stmt.where(AuditLog.staff_name == staff_name)
    if target:
        stmt = stmt.where(AuditLog.target_id == target)
    if date_from:
        stmt = stmt.where(AuditLog.created_at >= date_from[:10])
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * 100).limit(100)).all()
    return {"total": total, "items": [{"id": a.id, "staff": a.staff_name, "action": a.action, "target_type": a.target_type,
                                       "target_id": a.target_id, "details": a.details, "ip": a.ip, "created_at": a.created_at} for a in rows]}


def backups_dir() -> Path:
    return Path(settings().backup_dir)


def backups() -> dict:
    """Backups written by the `backup` service (nightly, plus on request) and its last restore check."""
    folder = backups_dir()
    files = sorted(folder.glob("*.tar"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
    check = {}
    if (folder / "restore-check.json").is_file():
        try:
            check = json.loads((folder / "restore-check.json").read_text(encoding="utf-8"))
        except ValueError:
            check = {"status": "unreadable"}
    return {"items": [{"name": p.name, "size": p.stat().st_size, "created_at": p.stat().st_mtime} for p in files[:30]],
            "restore_check": check, "requested": (folder / "backup.request").exists()}


@router.get("/system")
def system(staff=Depends(staff_required), db=Depends(session)):
    bot = db.get(BotState, "support")
    checks = {}
    for name, probe in (("database", lambda: db.execute(text("SELECT 1"))), ("redis", lambda: redis_client().ping())):
        try:
            probe()
            checks[name] = "ok"
        except Exception:
            checks[name] = "unavailable"
    online = bool(bot and bot.last_seen and aware(bot.last_seen) > now() - timedelta(seconds=120))
    alive, last_run = options.scheduler_alive(db)
    checks["scheduler"] = "ok" if alive else "stopped"  # beat + worker: SLA, reminders, summaries, scheduled announcements
    return {"environment": settings().environment, "checks": checks, "scheduler_seen": last_run,
            "bot": {"username": bot.username if bot else "", "mode": bot.mode if bot else "", "online": online,
                    "last_seen": bot.last_seen if bot else None},
            "queue": queue_state(staff, db), "errors_24h": db.scalar(select(func.count()).select_from(ErrorLog).where(
                ErrorLog.created_at >= now() - timedelta(days=1))), "backups": backups()}


@router.get("/system/queue")
def queue_state(staff=Depends(staff_required), db=Depends(session)):
    messages = dict(db.execute(select(Message.delivery, func.count()).where(Message.delivery.in_(("queued", "sending", "failed")))
                               .group_by(Message.delivery)).all())
    deliveries = dict(db.execute(select(CampaignDelivery.status, func.count()).where(CampaignDelivery.status.in_(("pending", "sending")))
                                 .group_by(CampaignDelivery.status)).all())
    oldest = db.scalar(select(func.min(Message.created_at)).where(Message.delivery == "queued"))
    return {"messages": messages, "campaign_deliveries": deliveries, "oldest_queued": oldest}


@router.get("/system/errors")
def errors(source: str = "", page: int = Query(1, ge=1), staff=Depends(staff_required), db=Depends(session)):
    stmt = select(ErrorLog)
    if source:
        stmt = stmt.where(ErrorLog.source == source)
    rows = db.scalars(stmt.order_by(ErrorLog.id.desc()).offset((page - 1) * 100).limit(100)).all()
    return [{"id": e.id, "source": e.source, "kind": e.kind, "message": e.message, "created_at": e.created_at} for e in rows]


@router.post("/system/errors/clear")
def clear_errors(request: Request, staff=Depends(staff_required), db=Depends(session)):
    db.execute(delete(ErrorLog))
    audit.record(db, staff, "system.errors_clear", "system", "errors", {}, request.state.ip)
    db.commit()
    return {"ok": True}


@router.post("/system/reconcile")
def reconcile(staff=Depends(staff_required)):
    """Restart deliveries that are waiting (normally done by the scheduler every minute)."""
    queue.task("workers.tasks.reconcile")
    return {"ok": True}


@router.get("/system/backups")
def list_backups(staff=Depends(staff_required)):
    return backups()


@router.post("/system/backups")
def request_backup(request: Request, staff=Depends(staff_required), db=Depends(session)):
    """Asks the backup service for a backup now (it checks for requests every minute)."""
    folder = backups_dir()
    if not folder.is_dir():
        raise HTTPException(409, "backup_service_missing")
    (folder / "backup.request").write_text(staff.username, encoding="utf-8")
    audit.record(db, staff, "system.backup", "system", "backup", {}, request.state.ip)
    db.commit()
    return backups()


@router.get("/system/backups/{name}")
def download_backup(name: str, request: Request, staff=Depends(staff_required), db=Depends(session)):
    path = (backups_dir() / name).resolve()
    if path.parent != backups_dir().resolve() or path.suffix != ".tar" or not path.is_file():
        raise HTTPException(404, "not_found")
    audit.record(db, staff, "system.backup_download", "system", name, {}, request.state.ip)
    db.commit()
    return FileResponse(path, media_type="application/x-tar", filename=name)
