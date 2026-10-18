from __future__ import annotations
from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import items
from app.auth import CurrentUser, create_token, get_current_user, verify_password
from app.db import get_db
from app.errors import AppError, not_found
from app.idempotency import request_hash, run_mutation
from app.models import ItemType, Notification, Priority, Role, Status, Team, User
from app.schemas import (
    ActivityOut,
    ActivityPage,
    AssignIn,
    CommentIn,
    DashboardCounts,
    ItemCreate,
    ItemOut,
    ItemPage,
    ItemSummary,
    ItemUpdate,
    LoginIn,
    MemberOut,
    MembershipOut,
    MeOut,
    NotificationOut,
    TeamRef,
    TokenOut,
    TransitionIn,
    UserRef,
)

router = APIRouter()

IdempotencyKeyHeader = Header(default=None, alias="Idempotency-Key")


def required_version(if_match: str | None = Header(default=None, alias="If-Match")) -> int:
    """Writes that change an item must say which version they were based on."""
    if if_match is None:
        raise AppError(428, "precondition_required", "Send If-Match with the item version you are updating")
    try:
        return int(if_match.strip().strip('"').removeprefix("W/").strip('"'))
    except ValueError:
        raise AppError(400, "invalid_if_match", "If-Match must be the item's version number")


# --- auth ---------------------------------------------------------------------------------------------------


@router.post("/auth/login", response_model=TokenOut, tags=["auth"])
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email == data.email.lower().strip())).scalar_one_or_none()
    if user is None or not verify_password(data.password, user.password_hash):
        raise AppError(401, "invalid_credentials", "Email or password is incorrect")
    return TokenOut(access_token=create_token(user.id))


@router.get("/me", response_model=MeOut, tags=["auth"])
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.is_admin:
        all_teams = db.execute(select(Team).order_by(Team.id)).scalars().all()
        return MeOut(
            id=user.id,
            name=user.name,
            email=user.email,
            is_admin=user.is_admin,
            memberships=[
                MembershipOut(team=TeamRef.model_validate(t), role=Role.LEAD) for t in all_teams
            ],
        )
    teams = {t.id: t for t in db.execute(select(Team).where(Team.id.in_(user.team_ids))).scalars()}
    return MeOut(
        id=user.id,
        name=user.name,
        email=user.email,
        is_admin=user.is_admin,
        memberships=[
            MembershipOut(team=TeamRef.model_validate(teams[tid]), role=role) for tid, role in user.roles.items()
        ],
    )


@router.get("/teams/{team_id}/members", response_model=list[MemberOut], tags=["teams"])
def team_members(team_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return [MemberOut(user=UserRef.model_validate(u), role=role) for u, role in items.team_members(db, user, team_id)]


# --- items: reads -------------------------------------------------------------------------------------------


@router.get("/items", response_model=ItemPage, tags=["items"])
def list_items(
    view: str | None = Query(default=None, description="assigned_to_me | unassigned_urgent | awaiting_my_approval"),
    team_id: int | None = Query(default=None),
    team: int | None = Query(default=None, description="Alias for team_id"),
    status: list[Status] | None = Query(default=None),
    priority: list[Priority] | None = Query(default=None),
    type: list[ItemType] | None = Query(default=None),
    assignee: str | None = Query(default=None, description="me | none | <user id>"),
    q: str | None = Query(default=None, max_length=200),
    sort: str = "-updated_at",
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    effective_team = team_id if team_id is not None else team
    return items.list_items(
        db,
        user,
        view=view,
        team_id=effective_team,
        status=status,
        priority=priority,
        type=type,
        assignee=assignee,
        q=q,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@router.get("/dashboard", response_model=DashboardCounts, tags=["items"])
def dashboard(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return items.dashboard_counts(db, user)


@router.get("/items/{item_id}", response_model=ItemOut, tags=["items"])
def get_item(item_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return items.item_out(user, items.load_item(db, user, item_id))


@router.get("/items/{item_id}/similar", response_model=list[ItemSummary], tags=["items"])
def similar_items(
    item_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return items.similar_items(db, user, item_id, limit)


@router.get("/items/{item_id}/activity", response_model=ActivityPage, tags=["items"])
def item_activity(
    item_id: int,
    cursor: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return items.list_activity(db, user, item_id, cursor, limit)


# --- items: writes ------------------------------------------------------------------------------------------


@router.post("/items", response_model=ItemOut, status_code=201, tags=["items"])
def create_item(
    data: ItemCreate,
    request: Request,
    key: str | None = IdempotencyKeyHeader,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return run_mutation(
        db, user, key, request_hash("POST", request.url.path, data), lambda: items.create_item(db, user, data), 201
    )


@router.patch("/items/{item_id}", response_model=ItemOut, tags=["items"])
def update_item(
    item_id: int,
    data: ItemUpdate,
    request: Request,
    version: int = Depends(required_version),
    key: str | None = IdempotencyKeyHeader,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return run_mutation(
        db,
        user,
        key,
        request_hash("PATCH", request.url.path, data, version),
        lambda: items.update_item(db, user, item_id, version, data),
    )


@router.post("/items/{item_id}/claim", response_model=ItemOut, tags=["items"])
def claim_item(
    item_id: int,
    request: Request,
    key: str | None = IdempotencyKeyHeader,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return run_mutation(
        db, user, key, request_hash("POST", request.url.path, None), lambda: items.claim_item(db, user, item_id)
    )


@router.post("/items/{item_id}/assign", response_model=ItemOut, tags=["items"])
def assign_item(
    item_id: int,
    data: AssignIn,
    request: Request,
    version: int = Depends(required_version),
    key: str | None = IdempotencyKeyHeader,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return run_mutation(
        db,
        user,
        key,
        request_hash("POST", request.url.path, data, version),
        lambda: items.assign_item(db, user, item_id, version, data.assignee_id),
    )


@router.post("/items/{item_id}/transitions", response_model=ItemOut, tags=["items"])
def transition_item(
    item_id: int,
    data: TransitionIn,
    request: Request,
    version: int = Depends(required_version),
    key: str | None = IdempotencyKeyHeader,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return run_mutation(
        db,
        user,
        key,
        request_hash("POST", request.url.path, data, version),
        lambda: items.transition_item(db, user, item_id, version, data.action, data.comment),
    )


@router.post("/items/{item_id}/comments", response_model=ActivityOut, status_code=201, tags=["items"])
def add_comment(
    item_id: int,
    data: CommentIn,
    request: Request,
    key: str | None = IdempotencyKeyHeader,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return run_mutation(
        db,
        user,
        key,
        request_hash("POST", request.url.path, data),
        lambda: items.add_comment(db, user, item_id, data.body),
        201,
    )


# --- notifications ------------------------------------------------------------------------------------------


@router.get("/notifications", response_model=list[NotificationOut], tags=["notifications"])
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    return db.execute(query.order_by(Notification.id.desc()).limit(limit)).scalars().all()


@router.post("/notifications/read-all", status_code=204, tags=["notifications"])
def mark_all_read(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=func.now())
    )
    db.commit()


@router.post("/notifications/{notification_id}/read", status_code=204, tags=["notifications"])
def mark_read(notification_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    result = db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user.id)
        .values(read_at=func.coalesce(Notification.read_at, func.now()))
    )
    if result.rowcount == 0:
        raise not_found("Notification")
    db.commit()
