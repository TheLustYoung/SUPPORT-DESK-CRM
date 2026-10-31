"""Staff sign-in: argon2 password hashes, signed session tokens in an HttpOnly cookie, optional
two-factor codes (TOTP), lockout after repeated wrong passwords. A session carries the account's session_version:
a new password, role or block ends every older session at once."""
import hashlib
import hmac
import secrets
import time
from datetime import timedelta
import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import func, select
from backend.config import settings
from backend.models import Staff, now

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))  # same work for unknown names: no username probing
AUDIENCE = "support-admin"
MIN_PASSWORD = 12
LOCK_AFTER, LOCK_FOR = 5, timedelta(minutes=15)


def password_problem(password: str) -> str | None:
    if len(password or "") < MIN_PASSWORD:
        return "short_password"
    if password.isdigit() or password.isalpha() or len(set(password)) < 5:
        return "weak_password"
    return None


def authenticate(db, username: str, password: str, code: str = "") -> tuple[Staff | None, str]:
    """(account, "") on success, else (None, reason): wrong, locked, code_required, code_wrong."""
    staff = db.scalar(select(Staff).where(func.lower(Staff.username) == (username or "").strip().lower()))
    if staff and staff.locked_until and staff.locked_until > now():
        return None, "locked"
    try:
        valid = hasher.verify(staff.password_hash if staff else DUMMY_HASH, password or "")
    except VerificationError:
        valid = False
    if not valid or not staff or not staff.active:
        if staff:
            staff.failed_logins += 1
            if staff.failed_logins >= LOCK_AFTER:
                staff.locked_until, staff.failed_logins = now() + LOCK_FOR, 0
            db.commit()
        return None, "wrong"
    if staff.totp_secret:
        if not code:
            return None, "code_required"
        if not totp_ok(staff.totp_secret, code):
            return None, "code_wrong"
    staff.failed_logins, staff.locked_until, staff.last_login_at = 0, None, now()
    db.commit()
    return staff, ""


def token_for(staff: Staff) -> str:
    moment = now()
    return jwt.encode({"sub": str(staff.id), "v": staff.session_version or 1, "iat": moment,
                       "exp": moment + timedelta(hours=settings().session_hours), "aud": AUDIENCE},
                      settings().jwt_secret.get_secret_value(), algorithm="HS256")


def staff_from_token(db, token: str) -> Staff | None:
    try:
        claims = jwt.decode(token or "", settings().jwt_secret.get_secret_value(), algorithms=["HS256"], audience=AUDIENCE)
        staff = db.get(Staff, int(claims.get("sub")))
    except (jwt.PyJWTError, TypeError, ValueError):
        return None
    if not staff or not staff.active or claims.get("v", 1) != (staff.session_version or 1):
        return None
    return staff


def new_totp() -> str:
    return pyotp.random_base32()


def totp_uri(secret_value: str, username: str, issuer: str) -> str:
    return pyotp.TOTP(secret_value).provisioning_uri(name=username, issuer_name=issuer)


def totp_ok(secret_value: str, code: str) -> bool:
    return pyotp.TOTP(secret_value).verify((code or "").replace(" ", ""), valid_window=1)


# ---------------------------------------------------------------- linking Telegram for notifications
def _key() -> bytes:
    return hashlib.sha256(b"link|" + settings().jwt_secret.get_secret_value().encode()).digest()


def _link(staff_id: int, version: int, window: int) -> str:
    digest = hmac.new(_key(), f"{staff_id}|{version}|{window}".encode(), hashlib.sha256).hexdigest()[:8].upper()
    return f"{staff_id}-{digest}"


def link_code(staff: Staff) -> str:
    """Short code the agent sends to the bot (/link CODE) to get notifications in Telegram; valid for 10–20 minutes."""
    return _link(staff.id, staff.session_version or 1, int(time.time() // 600))


def staff_by_link_code(db, code: str) -> Staff | None:
    try:
        staff = db.get(Staff, int((code or "").split("-", 1)[0]))
    except ValueError:
        return None
    if not staff or not staff.active:
        return None
    window = int(time.time() // 600)
    if any(hmac.compare_digest(code.strip().upper(), _link(staff.id, staff.session_version or 1, w)) for w in (window, window - 1)):
        return staff
    return None
