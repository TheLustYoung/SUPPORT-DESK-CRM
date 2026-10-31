"""Second audit (Н1–Н6): the visitor's address behind proxies, formulas in exports, closed API docs, scheduler
monitoring, retention of logs."""
from datetime import timedelta
from starlette.requests import Request
from backend.api.deps import client_ip
from backend.models import ErrorLog, StaffNotification, now
from backend.services import analytics, options
from tests.integration.test_tickets import new_ticket
from tests.integration.test_workers import worker_db  # noqa: F401 (fixture)


def request_from(peer: str, real_ip: str | None = None) -> Request:
    headers = [(b"x-real-ip", real_ip.encode())] if real_ip else []
    return Request({"type": "http", "method": "POST", "path": "/api/auth/login", "headers": headers, "client": (peer, 50000)})


def test_visitor_address_is_taken_from_the_trusted_proxy_only():
    assert client_ip(request_from("172.18.0.5", "203.0.113.7")) == "203.0.113.7"  # the dashboard's nginx in Docker
    assert client_ip(request_from("127.0.0.1", "2001:db8::1")) == "2001:db8::1"
    assert client_ip(request_from("198.51.100.9", "203.0.113.7")) == "198.51.100.9"  # a stranger cannot pick an address
    assert client_ip(request_from("172.18.0.5", "not an ip")) == "172.18.0.5"
    assert client_ip(request_from("172.18.0.5")) == "172.18.0.5"


def test_one_attacker_does_not_lock_everybody_out(client, admin):
    """Ten wrong passwords from one address: that address waits, the others still sign in (the limit is per visitor)."""
    import backend.api.auth as auth_api
    from tests.integration.conftest import PASSWORD
    visitor = {"address": "203.0.113.66"}
    original = auth_api.client_ip
    auth_api.client_ip = lambda request: visitor["address"]
    try:
        statuses = [client.post("/api/auth/login", json={"username": "nobody", "password": "wrong-password-1"}).status_code for _ in range(11)]
        assert statuses[-1] == 429
        visitor["address"] = "198.51.100.20"  # a colleague somewhere else
        assert client.post("/api/auth/login", json={"username": "root", "password": PASSWORD}).status_code == 200
    finally:
        auth_api.client_ip = original


def test_exports_never_contain_formulas():
    report = {"missing": [{"query": '=HYPERLINK("http://example.invalid","x")', "count": 1}, {"query": "+1 555", "count": 1},
                          {"query": "@SUM(A1)", "count": 1}, {"query": "how to pay", "count": 2}],
              "by_language": {"=cmd": 1}, "total": -3}
    csv = analytics.to_csv(report).decode("utf-8-sig")
    assert "'=HYPERLINK" in csv and "'+1 555" in csv and "'@SUM" in csv and "'=cmd" in csv and "how to pay" in csv
    assert "-3" in csv  # numbers stay numbers
    import io
    import openpyxl
    book = openpyxl.load_workbook(io.BytesIO(analytics.to_xlsx(report)))
    assert all(cell.data_type != "f" for sheet in book for row in sheet.iter_rows() for cell in row)


def test_api_map_is_not_published_in_production(client):
    assert client.get("/api/openapi.json").status_code == 404
    assert client.get("/api/docs").status_code == 404


def test_system_page_shows_a_stopped_scheduler(admin, worker_db):
    from workers import tasks
    assert admin.get("/api/admin/system").json()["checks"]["scheduler"] == "stopped"  # never ran
    tasks.check_tickets()
    worker_db.expire_all()
    state = admin.get("/api/admin/system").json()
    assert state["checks"]["scheduler"] == "ok" and state["scheduler_seen"]
    options.put(worker_db, "scheduler_seen", (now() - timedelta(minutes=5)).isoformat())
    worker_db.commit()
    assert admin.get("/api/admin/system").json()["checks"]["scheduler"] == "stopped"


def test_old_errors_and_notifications_are_removed(bot, admin, worker_db):
    from workers import tasks
    new_ticket(bot)  # creates a notification for the administrator
    old = now() - timedelta(days=100)
    worker_db.add(ErrorLog(source="worker", kind="test", message="old", created_at=old))
    worker_db.add(ErrorLog(source="worker", kind="test", message="fresh"))
    for note in worker_db.query(StaffNotification):
        note.created_at = old
    worker_db.commit()
    tasks.retention()
    worker_db.expire_all()
    assert [e.message for e in worker_db.query(ErrorLog)] == ["fresh"]
    assert worker_db.query(StaffNotification).count() == 0
