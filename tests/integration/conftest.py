import os
import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend.config import settings
from backend.database import Base, session
from backend.main import app
from backend.models import Staff, StaffDepartment
from backend.services import limits, queue, setup
from backend.services.auth import hasher

PASSWORD = "test-password-123"
POSTGRES_URL = os.getenv("TEST_DATABASE_URL")  # e.g. postgresql+psycopg://support:...@127.0.0.1:5432/support_test


@pytest.fixture(autouse=True)
def fake_infrastructure(monkeypatch):
    """In-process Redis and a list instead of Celery: tests see which tasks would be started."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(limits, "redis_client", lambda: redis)
    started = []
    monkeypatch.setattr(queue, "task", lambda name, *args, countdown=0: started.append((name, *args)))
    return started


@pytest.fixture
def db():
    """SQLite in memory by default; a real PostgreSQL when TEST_DATABASE_URL is set (never production!)."""
    if POSTGRES_URL:
        engine = create_engine(POSTGRES_URL)
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as db:
        setup.seed(db)
        yield db
        db.rollback()
    if POSTGRES_URL:
        Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(db):
    app.dependency_overrides[session] = lambda: db
    with TestClient(app) as client:
        client.headers["Origin"] = settings().admin_origin
        yield client
    app.dependency_overrides.clear()


def make_staff(db, username: str, role: str = "super_admin", departments=()) -> Staff:
    staff = Staff(username=username, password_hash=hasher.hash(PASSWORD), role_id=role)
    db.add(staff)
    db.flush()
    for department_id in departments:
        db.add(StaffDepartment(staff_id=staff.id, department_id=department_id))
    db.commit()
    return staff


def login(client, username: str, password: str = PASSWORD) -> None:
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    assert "HttpOnly" in response.headers["set-cookie"]


@pytest.fixture
def admin(client, db):
    make_staff(db, "root")
    login(client, "root")
    return client


@pytest.fixture
def bot(db):
    """The bot's client: only the internal API, with the service key."""
    app.dependency_overrides[session] = lambda: db
    with TestClient(app) as client:
        client.headers["X-Service-Key"] = settings().bot_service_key.get_secret_value()
        yield client
    app.dependency_overrides.clear()
