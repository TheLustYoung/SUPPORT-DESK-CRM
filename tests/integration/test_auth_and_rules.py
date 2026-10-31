"""Sign-in security and the permission rules of the dashboard API."""
import pyotp
from backend.main import app
from backend.models import AuditLog
from backend.services import team
from tests.integration.conftest import PASSWORD, login, make_staff


def test_every_admin_route_has_a_rule():
    """A new endpoint is never reachable by accident: each /api/admin route matches a rule."""
    spec = app.openapi()
    missing = []
    everything = frozenset(p for _, _, p in team.RULES if isinstance(p, str)) | {p for _, _, ps in team.RULES if isinstance(ps, tuple) for p in ps}
    for path, methods in spec["paths"].items():
        if not path.startswith("/api/admin"):
            continue
        sample = path.replace("{ticket_id}", "1").replace("{user_id}", "1").replace("{campaign_id}", "1").replace("{node_id}", "1") \
            .replace("{version_id}", "1").replace("{asset_id}", "1").replace("{template_id}", "1").replace("{message_id}", "1") \
            .replace("{staff_id}", "1").replace("{role_id}", "r-1").replace("{lang}", "en").replace("{tag}", "vip") \
            .replace("{name}", "tickets").replace("{action}", "pause").replace("{code}", "en").replace("{department_id}", "1") \
            .replace("{category_id}", "1").replace("{key}", "open").replace("{rule_id}", "1")
        for method in methods:
            if not team.allowed(everything, method.upper(), sample):
                missing.append(f"{method.upper()} {path}")
    assert not missing


def test_origin_check_blocks_cross_site_changes(client, db):
    make_staff(db, "root")
    client.headers["Origin"] = "https://evil.example"
    assert client.post("/api/auth/login", json={"username": "root", "password": PASSWORD}).status_code == 403


def test_lockout_after_wrong_passwords(client, db):
    make_staff(db, "root")
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "root", "password": "wrong-password-1"}).status_code == 401
    locked = client.post("/api/auth/login", json={"username": "root", "password": PASSWORD})
    assert locked.status_code == 401 and locked.json()["detail"] == "locked"
    assert db.query(AuditLog).filter_by(action="auth.failed").count() >= 5


def test_two_factor_sign_in(admin, db):
    secret = admin.post("/api/admin/me/totp").json()["secret"]
    assert admin.post("/api/admin/me/totp/confirm", json={"code": "000000"}).status_code == 400
    assert admin.post("/api/admin/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}).json()["totp"] is True
    admin.cookies.clear()
    assert admin.post("/api/auth/login", json={"username": "root", "password": PASSWORD}).json()["detail"] == "code_required"
    ok = admin.post("/api/auth/login", json={"username": "root", "password": PASSWORD, "code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200


def test_password_change_signs_out_everywhere(admin):
    assert admin.post("/api/admin/me/password", json={"current": PASSWORD, "new": "another-password-456"}).status_code == 200
    assert admin.get("/api/admin/me").status_code == 401


def test_roles_cannot_grant_more_than_they_have(admin, db):
    make_staff(db, "boss", "supervisor")
    login(admin, "boss")
    assert admin.get("/api/admin/team").status_code == 403  # no team.manage
    login(admin, "root")
    created = admin.post("/api/admin/team/roles", json={"name": "Night shift", "permissions": ["tickets.view", "tickets.reply"]})
    assert created.status_code == 200
    account = admin.post("/api/admin/team/accounts", json={"username": "night", "password": "night-shift-2026", "role": created.json()["id"]})
    assert account.status_code == 200
    login(admin, "night", "night-shift-2026")
    assert admin.get("/api/admin/me").json()["permissions"] == ["tickets.reply", "tickets.view"]
    assert admin.get("/api/admin/settings").status_code == 403


def test_last_super_admin_is_kept(admin, db):
    me = admin.get("/api/admin/me").json()
    assert admin.patch(f"/api/admin/team/accounts/{me['id']}", json={"active": False}).json()["detail"] == "not_yourself"
