"""Staff accounts, roles and what each role may do in the dashboard.

- Built-in roles come from backend.services.permissions (the five roles of the specification); custom roles pick any
  set of permissions.
- Every /api/admin call is checked against RULES; a call that is not listed there is refused (super administrators
  included), so a new endpoint cannot become reachable by accident. Some calls check more inside (ticket visibility,
  sensitive user fields).
- Nobody can hand out more than they have: roles and accounts with rights beyond the actor's own are refused. Nobody
  changes their own role or blocks themselves, and the last active Super Administrator cannot be removed.
- A new role, password or block bumps session_version, which signs that person out everywhere at once.
"""
import re
import secrets
from sqlalchemy import delete, func, select
from backend.models import AuditLog, Role, Staff, StaffDepartment, now
from backend.services import audit
from backend.services.auth import hasher, password_problem
from backend.services.permissions import ALL, BUILTIN_ROLES, PERMISSIONS
from backend.services.security import DomainError

ANY = None  # any signed-in staff member
# (methods, path under /api/admin, permission). A tuple means "any of them".
_RULES = [
    ("GET", "/me", ANY), ("PATCH", "/me", ANY), ("POST", "/me/(password|totp|totp/confirm|totp/disable|telegram-code|telegram-unlink)", ANY),
    ("GET", "/overview", ANY), ("GET", "/search", ("tickets.view", "users.view")),
    ("GET|POST", "/notifications(/read)?", ANY),
    ("GET", "/lookups", ANY),
    # tickets
    ("GET", "/tickets(/\\d+)?", "tickets.view"), ("POST", "/tickets", "tickets.reply"),
    ("POST", "/tickets/\\d+/reply", "tickets.reply"), ("POST", "/tickets/\\d+/note", "tickets.notes"),
    ("POST", "/tickets/\\d+/(status|priority|category|close)", "tickets.update"), ("POST", "/tickets/\\d+/reopen", "tickets.reopen"),
    ("POST", "/tickets/\\d+/(assign|transfer)", "tickets.assign"),
    ("GET", "/messages/\\d+/file", ("tickets.view", "users.view")),
    # users
    ("GET", "/users(/\\d+)?", "users.view"), ("GET", "/users/\\d+/(timeline|tickets|messages)", "users.view"),
    ("POST|DELETE", "/users/\\d+/tags(/[^/]+)?", "users.edit"), ("POST", "/users/\\d+/notes", "users.edit"),
    ("POST", "/users/\\d+/(ban|unban)", "users.ban"), ("POST", "/users/\\d+/message", "users.message"),
    ("GET", "/users/\\d+/export", "users.export"), ("POST", "/users/\\d+/anonymize", "users.delete"),
    ("PUT", "/users/\\d+/contacts", "users.sensitive"),
    # campaigns
    ("GET", "/campaigns(/\\d+)?", ("campaigns.edit", "campaigns.send")), ("GET", "/campaigns/\\d+/deliveries", ("campaigns.edit", "campaigns.send")),
    ("POST", "/campaigns/count", ("campaigns.edit", "campaigns.send")),
    ("POST", "/campaigns", "campaigns.edit"), ("PUT|DELETE", "/campaigns/\\d+", "campaigns.edit"),
    ("POST", "/campaigns/\\d+/test", "campaigns.edit"),
    ("POST", "/campaigns/\\d+/(start|schedule|pause|resume|cancel|retry)", "campaigns.send"),
    # FAQ and bot content
    ("GET", "/faq(/versions)?", ("faq.edit", "faq.publish")), ("POST", "/faq/nodes", "faq.edit"),
    ("PATCH|DELETE", "/faq/nodes/\\d+", "faq.edit"), ("POST", "/faq/nodes/\\d+/move", "faq.edit"), ("POST", "/faq/reorder", "faq.edit"),
    ("POST", "/faq/preview", ("faq.edit", "faq.publish")),
    ("POST", "/faq/publish", "faq.publish"), ("POST", "/faq/versions/\\d+/restore", "faq.publish"),
    ("GET|PUT", "/content/welcome", "content.edit"), ("GET|PUT", "/content/menu", "content.edit"),
    ("GET", "/content/texts(/[\\w-]+)?", "content.edit"), ("PUT", "/content/texts/[\\w-]+", "content.edit"),
    ("POST", "/content/preview", ("content.edit", "faq.edit", "campaigns.edit")),
    ("GET", "/media", ("content.edit", "faq.edit", "campaigns.edit", "tickets.reply", "users.message")),
    ("POST", "/media", ("content.edit", "faq.edit", "campaigns.edit", "tickets.reply", "users.message")),
    ("GET", "/media/\\d+/file", ANY), ("DELETE", "/media/\\d+", "content.edit"),
    ("GET", "/templates", ("templates.edit", "tickets.reply")), ("POST", "/templates", "templates.edit"),
    ("PUT|DELETE", "/templates/\\d+", "templates.edit"),
    # analytics
    ("GET", "/analytics/(tickets|users|faq|agents|campaigns)", "analytics.view"),
    ("GET", "/analytics/(tickets|users|faq|agents|campaigns)/export", "analytics.export"),
    # settings
    ("GET", "/settings(/[a-z-]+)?", "settings.edit"), ("PATCH", "/settings/general", "settings.edit"),
    ("POST", "/settings/(languages|departments|categories|statuses|rules)", "settings.edit"),
    ("PATCH|DELETE", "/settings/(languages|departments|categories|statuses|rules)/[\\w-]+", "settings.edit"),
    ("PUT", "/settings/(transitions|sla)", "settings.edit"), ("POST", "/settings/(departments|categories|rules)/reorder", "settings.edit"),
    # team, audit, system
    ("GET|POST|PATCH|PUT|DELETE", "/team(/accounts|/roles)?(/[^/]+)?", "team.manage"),
    ("GET", "/audit", "audit.view"),
    ("GET", "/system(/errors|/queue|/backups)?", "system.manage"), ("POST", "/system/(backups|errors/clear|reconcile)", "system.manage"),
    ("GET", "/system/backups/[\\w.-]+", "system.manage"),
]
RULES = [(set(m.split("|")), re.compile("/api/admin" + p + r"\Z"), perm) for m, p, perm in _RULES]
USERNAME = re.compile(r"[A-Za-z0-9_.-]{2,56}\Z")
DELETED = "~"  # deleted accounts with audit history are renamed "name~abc123" and blocked (names never contain "~")


def allowed(perms: frozenset[str], method: str, path: str) -> bool:
    method = "GET" if method == "HEAD" else method
    for methods, pattern, permission in RULES:
        if method in methods and pattern.match(path):
            if permission is None:
                return True
            wanted = permission if isinstance(permission, tuple) else (permission,)
            return any(p in perms for p in wanted)
    return False


def roles(db) -> dict[str, dict]:
    result = {}
    used = dict(db.execute(select(Staff.role_id, func.count()).where(~Staff.username.contains(DELETED)).group_by(Staff.role_id)).all())
    for role in db.scalars(select(Role).order_by(Role.builtin.desc(), Role.name)):
        result[role.id] = {"id": role.id, "name": role.name, "description": role.description, "builtin": role.builtin,
                           "permissions": [p for p in PERMISSIONS if p in (role.permissions or [])], "accounts": used.get(role.id, 0)}
    return result


def public(db, staff: Staff) -> dict:
    departments = list(db.scalars(select(StaffDepartment.department_id).where(StaffDepartment.staff_id == staff.id)))
    return {"id": staff.id, "username": staff.username, "display_name": staff.display_name, "email": staff.email,
            "role": staff.role_id, "active": staff.active, "departments": departments, "languages": staff.languages or [],
            "max_open_tickets": staff.max_open_tickets, "totp": bool(staff.totp_secret), "telegram": bool(staff.telegram_id),
            "created_at": staff.created_at, "created_by": staff.created_by, "last_login_at": staff.last_login_at,
            "locked": bool(staff.locked_until and staff.locked_until > now())}


def _super_admins(db) -> int:
    return db.scalar(select(func.count()).select_from(Staff).where(Staff.role_id == "super_admin", Staff.active.is_(True)))


def _check_role(db, actor: Staff, role_id: str) -> Role:
    role = db.get(Role, role_id)
    if role is None:
        raise DomainError("unknown_role")
    if not set(role.permissions or []) <= actor.permissions:
        raise DomainError("role_above_you", 403)
    return role


def _check_target(actor: Staff, target: Staff) -> None:
    if not target.permissions <= actor.permissions:
        raise DomainError("above_you", 403)


def _departments(db, staff: Staff, ids: list[int] | None) -> None:
    if ids is None:
        return
    db.execute(delete(StaffDepartment).where(StaffDepartment.staff_id == staff.id))
    for department_id in sorted(set(ids)):
        db.add(StaffDepartment(staff_id=staff.id, department_id=department_id))


def _profile(staff: Staff, data: dict) -> None:
    if "display_name" in data:
        staff.display_name = str(data["display_name"] or "").strip()[:64]
    if "email" in data:
        staff.email = str(data["email"] or "").strip()[:255]
    if "languages" in data:
        staff.languages = [str(code)[:12] for code in (data["languages"] or [])][:30]
    if "max_open_tickets" in data:
        staff.max_open_tickets = max(0, min(int(data["max_open_tickets"] or 0), 1000))


def create_account(db, actor: Staff, data: dict, ip: str = "") -> Staff:
    username, password = str(data.get("username") or "").strip(), str(data.get("password") or "")
    if not USERNAME.match(username):
        raise DomainError("bad_username")
    problem = password_problem(password)
    if problem:
        raise DomainError(problem)
    _check_role(db, actor, str(data.get("role") or ""))
    if db.scalar(select(Staff).where(func.lower(Staff.username) == username.lower())):
        raise DomainError("username_taken")
    staff = Staff(username=username, password_hash=hasher.hash(password), role_id=data["role"], created_by=actor.username, created_at=now())
    _profile(staff, data)
    db.add(staff)
    db.flush()
    _departments(db, staff, data.get("departments") or [])
    audit.record(db, actor, "team.create", "staff", staff.username, {"role": staff.role_id}, ip)
    db.commit()
    return staff


def update_account(db, actor: Staff, target: Staff, data: dict, ip: str = "") -> Staff:
    role, active, password = data.get("role"), data.get("active"), data.get("password")
    if target.id == actor.id and (role is not None or active is False):
        raise DomainError("not_yourself")
    _check_target(actor, target)
    if role is not None:
        _check_role(db, actor, role)
    if password is not None and password_problem(password):
        raise DomainError(password_problem(password))
    losing = target.role_id == "super_admin" and target.active and ((role is not None and role != "super_admin") or active is False)
    if losing and _super_admins(db) <= 1:
        raise DomainError("last_super_admin")
    changes = {}
    if role is not None and role != target.role_id:
        changes["role"] = [target.role_id, role]
        target.role_id = role
        db.flush()
        db.refresh(target)
    if active is not None and bool(active) != target.active:
        changes["active"] = bool(active)
        target.active = bool(active)
    if password is not None:
        changes["password"] = "changed"
        target.password_hash = hasher.hash(password)
    if data.get("unlock"):
        target.locked_until, target.failed_logins = None, 0
        changes["unlocked"] = True
    if data.get("reset_totp"):
        target.totp_secret = None
        changes["totp"] = "reset"
    _profile(target, data)
    _departments(db, target, data.get("departments"))
    if {"role", "active", "password", "totp"} & changes.keys():
        target.session_version = (target.session_version or 1) + 1
    audit.record(db, actor, "team.update", "staff", target.username, changes, ip)
    db.commit()
    return target


def delete_account(db, actor: Staff, target: Staff, ip: str = "") -> None:
    """Accounts with history (audit log, tickets) are blocked and renamed instead of deleted, so history keeps who did what."""
    if target.id == actor.id:
        raise DomainError("not_yourself")
    _check_target(actor, target)
    if target.role_id == "super_admin" and target.active and _super_admins(db) <= 1:
        raise DomainError("last_super_admin")
    audit.record(db, actor, "team.delete", "staff", target.username, {}, ip)
    if db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.staff_id == target.id)):
        target.active, target.session_version = False, (target.session_version or 1) + 1
        target.username = f"{target.username[:56]}{DELETED}{secrets.token_hex(3)}"  # frees the name for a new account
    else:
        db.delete(target)
    db.commit()


def save_role(db, actor: Staff, role_id: str | None, name: str, wanted: list[str], description: str = "", ip: str = "") -> dict:
    name, wanted_set = (name or "").strip(), set(wanted or [])
    if not 1 <= len(name) <= 64:
        raise DomainError("bad_role_name")
    if not wanted_set <= ALL:
        raise DomainError("unknown_permission")
    if not wanted_set <= actor.permissions:
        raise DomainError("role_above_you", 403)
    if role_id is None:
        role = Role(id="r-" + secrets.token_hex(4), builtin=False)
        db.add(role)
    else:
        role = db.get(Role, role_id)
        if not role:
            raise DomainError("not_found", 404)
        if role.builtin:
            raise DomainError("builtin_role")
        if not set(role.permissions or []) <= actor.permissions:
            raise DomainError("role_above_you", 403)
    role.name, role.description, role.permissions = name, (description or "")[:255], [p for p in PERMISSIONS if p in wanted_set]
    db.flush()
    for staff in db.scalars(select(Staff).where(Staff.role_id == role.id)):  # changed rights: sign in again
        staff.session_version = (staff.session_version or 1) + 1
    audit.record(db, actor, "team.role", "role", role.id, {"name": name, "permissions": role.permissions}, ip)
    db.commit()
    return roles(db)[role.id]


def delete_role(db, actor: Staff, role_id: str, ip: str = "") -> None:
    role = db.get(Role, role_id)
    if not role:
        raise DomainError("not_found", 404)
    if role.builtin or role_id in BUILTIN_ROLES:
        raise DomainError("builtin_role")
    if not set(role.permissions or []) <= actor.permissions:
        raise DomainError("role_above_you", 403)
    if db.scalar(select(func.count()).select_from(Staff).where(Staff.role_id == role_id, ~Staff.username.contains(DELETED))):
        raise DomainError("role_in_use")
    for gone in db.scalars(select(Staff).where(Staff.role_id == role_id)):  # deleted accounts kept for the audit log
        gone.role_id = "reporting_admin"
    audit.record(db, actor, "team.role_delete", "role", role_id, {}, ip)
    db.delete(role)
    db.commit()
