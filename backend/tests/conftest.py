from __future__ import annotations
"""Tests run against a real PostgreSQL database, because the behaviours that matter most (row-level
races, conditional updates, unique constraints, SKIP LOCKED) are database behaviours that a mock or
SQLite would not reproduce honestly."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/ops_test")

from dataclasses import dataclass  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.auth import create_token  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Membership, Role, Team, User  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def schema():
    # The tests drop and recreate the whole schema. Refuse to run against anything but a test database,
    # so a stray DATABASE_URL can never wipe development or production data.
    if not (engine.url.database or "").endswith("_test"):
        pytest.exit(f"Refusing to run tests against database {engine.url.database!r}: its name must end in _test", 2)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL test database not reachable at {engine.url}: {exc}")
        yield
        return

    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    command.upgrade(cfg, "head")  # the tests also prove the migration builds the schema
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE notifications, outbox, idempotency_keys, activity_events, comments, work_items, "
                    "memberships, teams, users RESTART IDENTITY CASCADE"
                )
            )
    except Exception:
        pass


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


class Api:
    """A test client acting as one user."""

    def __init__(self, user: User):
        self.user = user
        self.client = TestClient(app)
        self.client.headers["Authorization"] = f"Bearer {create_token(user.id)}"

    def __getattr__(self, name):
        return getattr(self.client, name)

    def create_item(self, team_id: int, **fields):
        body = {"team_id": team_id, "type": "task", "title": "Investigate something", "priority": "P2", **fields}
        response = self.client.post("/api/items", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def transition(self, item: dict, action: str, comment: str | None = None, version: int | None = None):
        return self.client.post(
            f"/api/items/{item['id']}/transitions",
            json={"action": action, "comment": comment},
            headers={"If-Match": str(version if version is not None else item["version"])},
        )


@dataclass
class World:
    support: Team
    payments: Team
    lead: User  # Lead of Support and Payments
    member: User  # Member of Support and Payments
    member2: User  # Member of Support
    viewer: User  # Viewer of Support
    outsider: User  # Member of Payments only
    admin: User

    def api(self, user: User) -> Api:
        return Api(user)


@pytest.fixture
def world(db) -> World:
    support, payments = Team(name="Support"), Team(name="Payments")
    db.add_all([support, payments])
    users = {}
    for key in ("lead", "member", "member2", "viewer", "outsider", "admin"):
        users[key] = User(
            name=key.title(), email=f"{key}@test.dev", password_hash="x", is_admin=(key == "admin")
        )
        db.add(users[key])
    db.flush()
    roles = [
        ("lead", support, Role.LEAD),
        ("lead", payments, Role.LEAD),
        ("member", support, Role.MEMBER),
        ("member", payments, Role.MEMBER),
        ("member2", support, Role.MEMBER),
        ("viewer", support, Role.VIEWER),
        ("outsider", payments, Role.MEMBER),
    ]
    for key, team, role in roles:
        db.add(Membership(user_id=users[key].id, team_id=team.id, role=role))
    db.commit()
    return World(support=support, payments=payments, **users)
