from __future__ import annotations
from dataclasses import dataclass, field
try:
    from datetime import UTC, datetime, timedelta
except ImportError:
    from datetime import datetime, timedelta, timezone
    UTC = timezone.utc

import bcrypt
import jwt
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import AppError
from app.models import Membership, Role, User


@dataclass(frozen=True)
class CurrentUser:
    """The caller, with their role in each team they belong to. Loaded once per request."""

    id: int
    name: str
    email: str
    is_admin: bool
    roles: dict[int, Role] = field(default_factory=dict)

    @property
    def team_ids(self) -> list[int]:
        return list(self.roles)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user_id: int) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=settings.jwt_ttl_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def load_current_user(db: Session, user_id: int) -> CurrentUser | None:
    user = db.get(User, user_id)
    if user is None:
        return None
    rows = db.execute(select(Membership.team_id, Membership.role).where(Membership.user_id == user_id)).all()
    return CurrentUser(
        id=user.id,
        name=user.name,
        email=user.email,
        is_admin=user.is_admin,
        roles={team_id: Role(role) for team_id, role in rows},
    )


def get_current_user(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> CurrentUser:
    unauthenticated = AppError(401, "unauthenticated", "Missing or invalid token")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthenticated
    try:
        payload = jwt.decode(authorization[7:], settings.jwt_secret, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthenticated
    # Roles are read from the database on every request, so a removed membership takes effect immediately.
    user = load_current_user(db, user_id)
    if user is None:
        raise unauthenticated
    return user
