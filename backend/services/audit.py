"""Audit log of sensitive operations: permission changes, bans, exports, ticket status changes and
configuration updates. Entries are written in the same transaction as the change they describe."""
from backend.models import AuditLog, ErrorLog, Staff


def record(db, actor: Staff | None, action: str, target_type: str = "", target_id="", details: dict | None = None,
           ip: str = "") -> None:
    db.add(AuditLog(staff_id=actor.id if actor else None, staff_name=actor.username if actor else "system", action=action,
                    target_type=target_type, target_id=str(target_id)[:64], details=details or {}, ip=(ip or "")[:64]))


def error(db, source: str, kind: str, message: str) -> None:
    db.add(ErrorLog(source=source, kind=kind[:64], message=str(message)[:1000]))
