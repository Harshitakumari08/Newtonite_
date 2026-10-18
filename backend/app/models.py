from __future__ import annotations
from datetime import datetime
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum
    class StrEnum(str, Enum):
        pass
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Role(StrEnum):
    VIEWER = "VIEWER"
    MEMBER = "MEMBER"
    LEAD = "LEAD"


class ItemType(StrEnum):
    INCIDENT = "incident"
    CUSTOMER_ISSUE = "customer_issue"
    PAYMENT = "payment"
    COMPLIANCE = "compliance"
    TASK = "task"


class Priority(StrEnum):
    # String values sort correctly: P1 (most urgent) < P4.
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class Status(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


ACTIVE_STATUSES = (Status.OPEN, Status.IN_PROGRESS, Status.AWAITING_APPROVAL)
TERMINAL_STATUSES = (Status.CLOSED, Status.CANCELLED)
APPROVAL_REQUIRED_TYPES = (ItemType.PAYMENT, ItemType.COMPLIANCE)


def _check_in(column: str, enum: type[StrEnum]) -> CheckConstraint:
    values = ", ".join(f"'{v.value}'" for v in enum)
    return CheckConstraint(f"{column} IN ({values})", name=f"ck_{column}_valid")


def _in_list(values) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (_check_in("role", Role), Index("ix_memberships_team", "team_id", "role"))

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))

    user: Mapped[User] = relationship(back_populates="memberships")
    team: Mapped[Team] = relationship()


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (
        _check_in("type", ItemType),
        _check_in("priority", Priority),
        _check_in("status", Status),
        # An item that someone is working on must have an owner.
        CheckConstraint(
            "status NOT IN ('IN_PROGRESS', 'AWAITING_APPROVAL') OR assignee_id IS NOT NULL",
            name="ck_active_work_has_assignee",
        ),
        # Default list order and "my team" views.
        Index("ix_items_team_updated", "team_id", text("updated_at DESC"), text("id DESC")),
        Index("ix_items_team_priority", "team_id", "priority", "created_at", "id"),
        # "Assigned to me" only ever looks at active work.
        Index(
            "ix_items_assignee_active",
            "assignee_id",
            text("updated_at DESC"),
            postgresql_where=text(f"status IN ({_in_list(ACTIVE_STATUSES)})"),
        ),
        # "Unassigned urgent".
        Index(
            "ix_items_unassigned_open",
            "team_id",
            "priority",
            "created_at",
            postgresql_where=text("assignee_id IS NULL AND status = 'OPEN'"),
        ),
        # "Awaiting my approval".
        Index(
            "ix_items_awaiting_approval",
            "team_id",
            text("updated_at DESC"),
            postgresql_where=text("status = 'AWAITING_APPROVAL'"),
        ),
        Index("ix_items_search", "search_tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    priority: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(32), default=Status.OPEN, server_default=Status.OPEN.value)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    search_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))", persisted=True),
        deferred=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Who sent the item for approval; that person can't approve it either (four-eyes rule).
    submitted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))

    team: Mapped[Team] = relationship(lazy="joined")
    assignee: Mapped[Optional[User]] = relationship(foreign_keys=[assignee_id], lazy="joined")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id], lazy="joined")


class EventKind(StrEnum):
    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    PRIORITY_CHANGED = "priority_changed"
    EDITED = "edited"
    COMMENTED = "commented"


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (Index("ix_comments_item", "item_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActivityEvent(Base):
    """Append-only history. Rows are never updated or deleted."""

    __tablename__ = "activity_events"
    __table_args__ = (_check_in("kind", EventKind), Index("ix_activity_item", "item_id", text("id DESC")))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32))
    from_value: Mapped[Optional[Any]] = mapped_column(JSONB)
    to_value: Mapped[Optional[Any]] = mapped_column(JSONB)
    comment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("comments.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    actor: Mapped[User] = relationship(lazy="joined")
    comment: Mapped[Optional[Comment]] = relationship(lazy="joined")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    response_body: Mapped[Optional[Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    DEAD = "dead"


class OutboxMessage(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        _check_in("status", OutboxStatus),
        Index("ix_outbox_pending", "available_at", "id", postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[Any] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default=OutboxStatus.PENDING, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # The same activity event can only notify a given user once, so re-running a job is harmless.
        UniqueConstraint("user_id", "event_id", name="uq_notifications_user_event"),
        Index("ix_notifications_user", "user_id", text("id DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id"))
    event_id: Mapped[int] = mapped_column(ForeignKey("activity_events.id"))
    message: Mapped[str] = mapped_column(Text)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
