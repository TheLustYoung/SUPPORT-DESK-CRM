"""Background tasks for the Celery workers, started by name (no worker code imported into the API).

Tasks that read rows written in the current transaction are sent only after the commit (`later`), so a worker never
looks for a message that is not saved yet. If the broker is down, the periodic `reconcile` task finds the queued
messages and deliveries in the database and starts them again, so a failure here is logged and not raised.
"""
import logging
from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
PENDING = "queue_pending"


def task(name: str, *args, countdown: float = 0) -> None:
    try:
        from workers.celery_app import celery
        celery.send_task(name, args=list(args), countdown=countdown or None)
    except Exception:
        log.warning('{"event":"task_enqueue_failed","task":"%s"}', name)


def later(db: Session, name: str, *args, countdown: float = 0) -> None:
    """Send the task once the caller's transaction is committed (dropped on rollback)."""
    db.info.setdefault(PENDING, []).append((name, args, countdown))


@event.listens_for(Session, "after_commit")
def _flush_pending(db: Session) -> None:
    for name, args, countdown in db.info.pop(PENDING, []):
        task(name, *args, countdown=countdown)


@event.listens_for(Session, "after_rollback")
def _drop_pending(db: Session) -> None:
    if not db.in_transaction():
        db.info.pop(PENDING, None)
