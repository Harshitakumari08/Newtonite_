from __future__ import annotations
import random
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.auth import create_token, hash_password, load_current_user, verify_password
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Membership, Role, Status, Team, User, WorkItem
from app.seed import TEAMS, USERS, seed

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import TSVECTOR, JSONB
from sqlalchemy.schema import Computed


@compiles(TSVECTOR, "sqlite")
def compile_tsvector_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(Computed, "sqlite")
def compile_computed_sqlite(element, compiler, **kw):
    return "AS ('') STORED"


# Isolated SQLite in-memory engine for unit/auth/model testing
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
def schema():
    """Override session schema fixture from conftest to enable standalone unit tests."""
    yield


@pytest.fixture
def db_session():
    # Create SQLite-compatible subset of tables
    # (Excluding postgres-specific TSVECTOR columns if necessary, but Base.metadata creates SQLite types cleanly)
    Base.metadata.create_all(bind=test_engine)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client(db_session: Session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- Authentication & Password Tests ---


def test_password_hashing_and_verification():
    raw_password = "SecureOperationalPassword123!"
    hashed = hash_password(raw_password)
    assert hashed != raw_password
    assert verify_password(raw_password, hashed) is True
    assert verify_password("WrongPassword123!", hashed) is False


def test_signed_jwt_creation_and_payload():
    user_id = 99
    token = create_token(user_id)
    assert isinstance(token, str)
    decoded = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    assert decoded["sub"] == "99"
    assert "exp" in decoded
    assert "iat" in decoded


def test_login_success(client: TestClient, db_session: Session):
    user = User(
        name="Alice Operator",
        email="alice@example.com",
        password_hash=hash_password("correct-pass"),
        is_admin=False,
    )
    db_session.add(user)
    db_session.commit()

    # Test both /api/auth/login and root /auth/login
    for path in ["/api/auth/login", "/auth/login"]:
        response = client.post(path, json={"email": "alice@example.com", "password": "correct-pass"})
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        payload = jwt.decode(data["access_token"], settings.jwt_secret, algorithms=["HS256"])
        assert payload["sub"] == str(user.id)


def test_login_invalid_password(client: TestClient, db_session: Session):
    user = User(
        name="Bob Worker",
        email="bob@example.com",
        password_hash=hash_password("real-pass"),
        is_admin=False,
    )
    db_session.add(user)
    db_session.commit()

    response = client.post("/api/auth/login", json={"email": "bob@example.com", "password": "wrong-password"})
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "invalid_credentials"


def test_login_nonexistent_email(client: TestClient):
    response = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "any-password"})
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "invalid_credentials"


def test_get_me_authenticated(client: TestClient, db_session: Session):
    support_team = Team(name="Support Team")
    payments_team = Team(name="Payments Team")
    db_session.add_all([support_team, payments_team])
    db_session.flush()

    user = User(
        name="Lead Operator",
        email="lead@example.com",
        password_hash=hash_password("secret"),
        is_admin=False,
    )
    db_session.add(user)
    db_session.flush()

    # User is LEAD in Support, MEMBER in Payments
    db_session.add(Membership(user_id=user.id, team_id=support_team.id, role=Role.LEAD))
    db_session.add(Membership(user_id=user.id, team_id=payments_team.id, role=Role.MEMBER))
    db_session.commit()

    token = create_token(user.id)
    headers = {"Authorization": f"Bearer {token}"}

    # Test both /api/me and /me
    for path in ["/api/me", "/me"]:
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == user.id
        assert data["email"] == "lead@example.com"
        assert data["name"] == "Lead Operator"
        assert len(data["memberships"]) == 2

        roles_by_team = {m["team"]["name"]: m["role"] for m in data["memberships"]}
        assert roles_by_team["Support Team"] == "LEAD"
        assert roles_by_team["Payments Team"] == "MEMBER"


def test_get_me_admin_returns_all_teams_as_lead(client: TestClient, db_session: Session):
    support_team = Team(name="Support Team")
    payments_team = Team(name="Payments Team")
    db_session.add_all([support_team, payments_team])
    db_session.flush()

    admin = User(
        name="Global Admin",
        email="admin@example.com",
        password_hash=hash_password("secret"),
        is_admin=True,
    )
    db_session.add(admin)
    db_session.commit()

    token = create_token(admin.id)
    headers = {"Authorization": f"Bearer {token}"}

    for path in ["/api/me", "/me"]:
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == admin.id
        assert data["email"] == "admin@example.com"
        assert data["is_admin"] is True
        assert len(data["memberships"]) == 2
        for m in data["memberships"]:
            assert m["role"] == "LEAD"


def test_unauthenticated_request_handling(client: TestClient):
    # No header
    assert client.get("/api/me").status_code == 401

    # Malformed header
    assert client.get("/api/me", headers={"Authorization": "InvalidScheme token"}).status_code == 401

    # Bad token
    assert client.get("/api/me", headers={"Authorization": "Bearer invalid.jwt.token"}).status_code == 401


# --- Role Membership & Data Model Tests ---


def test_role_membership_hierarchy(db_session: Session):
    team = Team(name="Engineering")
    db_session.add(team)
    db_session.flush()

    viewer = User(name="Viewer User", email="viewer@dev.local", password_hash="x")
    member = User(name="Member User", email="member@dev.local", password_hash="x")
    lead = User(name="Lead User", email="lead@dev.local", password_hash="x")
    db_session.add_all([viewer, member, lead])
    db_session.flush()

    db_session.add(Membership(user_id=viewer.id, team_id=team.id, role=Role.VIEWER))
    db_session.add(Membership(user_id=member.id, team_id=team.id, role=Role.MEMBER))
    db_session.add(Membership(user_id=lead.id, team_id=team.id, role=Role.LEAD))
    db_session.commit()

    current_viewer = load_current_user(db_session, viewer.id)
    current_member = load_current_user(db_session, member.id)
    current_lead = load_current_user(db_session, lead.id)

    assert current_viewer.roles[team.id] == Role.VIEWER
    assert current_member.roles[team.id] == Role.MEMBER
    assert current_lead.roles[team.id] == Role.LEAD


def test_work_item_attributes_and_defaults(db_session: Session):
    team = Team(name="Operations")
    user = User(name="Creator User", email="creator@dev.local", password_hash="x")
    db_session.add_all([team, user])
    db_session.flush()

    item = WorkItem(
        team_id=team.id,
        created_by_id=user.id,
        type="incident",
        title="High Memory Usage in Production",
        description="Container pods restarting frequently",
        priority="P1",
    )
    db_session.add(item)
    db_session.commit()

    loaded = db_session.execute(select(WorkItem).where(WorkItem.id == item.id)).scalar_one()
    assert loaded.version == 1
    assert loaded.status == Status.OPEN
    assert loaded.assignee_id is None
    assert loaded.requires_approval is False
    assert loaded.submitted_by_id is None
    assert loaded.created_at is not None
    assert loaded.updated_at is not None


def test_database_metadata_tables_and_indexes():
    """Verify that all required tables, constraints, and indexes exist in metadata."""
    tables = Base.metadata.tables

    expected_tables = [
        "users",
        "teams",
        "memberships",
        "work_items",
        "activity_events",
        "comments",
        "idempotency_keys",
        "outbox",
        "notifications",
    ]
    for table_name in expected_tables:
        assert table_name in tables, f"Expected table {table_name} not found in metadata"

    work_items_table = tables["work_items"]
    # Check required columns
    expected_cols = [
        "id", "team_id", "type", "title", "description", "priority",
        "status", "assignee_id", "created_by_id", "requires_approval",
        "version", "created_at", "updated_at", "submitted_by_id"
    ]
    for col in expected_cols:
        assert col in work_items_table.columns, f"Column {col} missing in work_items"

    # Check indexes
    index_names = {idx.name for idx in work_items_table.indexes}
    assert "ix_items_team_updated" in index_names
    assert "ix_items_team_priority" in index_names
    assert "ix_items_search" in index_names


def test_demo_seed_dataset(db_session: Session):
    """Verify demo seed function populates documented teams, users, and roles safely."""
    seed(db_session, n_items=0, rng=random.Random(42))

    # Verify all 4 documented teams
    teams = {t.name: t for t in db_session.execute(select(Team)).scalars()}
    for expected_team in TEAMS:
        assert expected_team in teams

    # Verify documented users exist and have secure password hashes
    users = {u.email: u for u in db_session.execute(select(User)).scalars()}
    for _, prefix, is_admin, roles in USERS:
        email = f"{prefix}@example.com"
        assert email in users
        assert verify_password("password", users[email].password_hash) is True
        assert users[email].is_admin == is_admin

    # Verify membership count
    memberships = db_session.execute(select(Membership)).scalars().all()
    assert len(memberships) > 0
