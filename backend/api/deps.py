import ipaddress
import secrets
from fastapi import Depends, HTTPException, Request
from backend.config import settings
from backend.database import session
from backend.models import Staff
from backend.services import team
from backend.services.auth import staff_from_token

COOKIE = "session"


def check_origin(request: Request):
    """CSRF protection for cookie-authenticated requests: changes only from the dashboard's own address."""
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") != settings().admin_origin:
        raise HTTPException(403, "invalid_origin")


def client_ip(request: Request) -> str:
    """The visitor's address for rate limits and the audit log. Requests reach the API through the dashboard's nginx,
    which puts the real address into X-Real-IP; the header is believed only from a trusted proxy (TRUSTED_PROXIES,
    private networks by default), so a visitor cannot forge it to dodge the limits."""
    peer = request.client.host if request.client else ""
    forwarded = request.headers.get("x-real-ip", "").strip()
    if forwarded and _trusted(peer):
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer


def _trusted(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in settings().trusted_networks)


def staff_required(request: Request, db=Depends(session)) -> Staff:
    check_origin(request)
    staff = staff_from_token(db, request.cookies.get(COOKIE, ""))
    if not staff:
        raise HTTPException(401, "auth_required")
    # Role check for the dashboard API (backend.services.team.RULES; calls not listed there are refused)
    if request.url.path.startswith("/api/admin") and not team.allowed(staff.permissions, request.method, request.url.path):
        raise HTTPException(403, "no_permission")
    request.state.ip = client_ip(request)
    return staff


def has(staff: Staff, permission: str) -> bool:
    return permission in staff.permissions


def bot_required(request: Request) -> str:
    """The bot process authenticates with its service key; /internal is closed to the outside in Plesk."""
    supplied = request.headers.get("x-service-key", "")
    if not secrets.compare_digest(supplied.encode(), settings().bot_service_key.get_secret_value().encode()):
        raise HTTPException(401, "unauthorized")
    return "support"


def require(db, model, id):
    item = db.get(model, id)
    if not item:
        raise HTTPException(404, "not_found")
    return item
