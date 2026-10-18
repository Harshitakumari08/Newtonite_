from __future__ import annotations
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import ItemType, Priority, Role, Status
from app.workflow import TransitionAction


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class TeamRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class MembershipOut(BaseModel):
    team: TeamRef
    role: Role


class MeOut(BaseModel):
    id: int
    name: str
    email: str
    is_admin: bool
    memberships: list[MembershipOut]


class MemberOut(BaseModel):
    user: UserRef
    role: Role


class ItemCreate(BaseModel):
    team_id: int
    type: ItemType
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=10_000)
    priority: Priority = Priority.P3
    due_at: datetime | None = None
    # Forced to true for payment and compliance items.
    requires_approval: bool = False


class ItemUpdate(BaseModel):
    """Partial update; only fields that are sent are changed."""

    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    priority: Priority | None = None
    due_at: datetime | None = None


class AssignIn(BaseModel):
    assignee_id: int


class TransitionIn(BaseModel):
    action: TransitionAction
    comment: str | None = Field(default=None, max_length=10_000)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class ItemSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    team: TeamRef
    type: ItemType
    title: str
    priority: Priority
    status: Status
    assignee: UserRef | None
    requires_approval: bool
    due_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class ItemOut(ItemSummary):
    description: str
    created_by: UserRef
    resolved_at: datetime | None
    # What the current user may do right now; the UI renders buttons from this, the server still re-checks.
    allowed_actions: list[str] = []


class ItemPage(BaseModel):
    items: list[ItemSummary]
    next_cursor: str | None


class DashboardCounts(BaseModel):
    assigned_to_me: int
    unassigned_urgent: int
    awaiting_my_approval: int


class ActivityOut(BaseModel):
    id: int
    kind: str
    actor: UserRef
    from_value: Any | None
    to_value: Any | None
    comment: str | None
    created_at: datetime


class ActivityPage(BaseModel):
    events: list[ActivityOut]
    next_cursor: str | None


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_id: int
    message: str
    read_at: datetime | None
    created_at: datetime
