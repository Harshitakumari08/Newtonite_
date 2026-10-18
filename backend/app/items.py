from __future__ import annotations
"""Work-item operations. Functions here never commit; the caller (app.idempotency.run_mutation) does,
so the item change, its activity events and its outbox rows always land in one transaction."""

import base64
import json
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, or_, select, text, tuple_, update
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.errors import AppError, not_found
from app.models import (
    ACTIVE_STATUSES,
    APPROVAL_REQUIRED_TYPES,
    ActivityEvent,
    Comment,
    EventKind,
    Membership,
    OutboxMessage,
    Priority,
    Role,
    Status,
    User,
    WorkItem,
)
from app.permissions import ROLE_RANK, Action, authorize, authorize_create, can, has_role, visible_team_ids
from app.schemas import (
    ActivityOut,
    ActivityPage,
    DashboardCounts,
    ItemCreate,
    ItemOut,
    ItemPage,
    ItemSummary,
    ItemUpdate,
    UserRef,
)
from app.workflow import TRANSITIONS, TransitionAction, transition_applies

URGENT = (Priority.P1, Priority.P2)


# ---------------------------------------------------------------------------------------------------------------
# Reading and presenting
# ---------------------------------------------------------------------------------------------------------------


def load_item(db: Session, user: CurrentUser, item_id: int, action: Action = Action.VIEW) -> WorkItem:
    item = db.get(WorkItem, item_id, populate_existing=True)
    if item is None:
        raise not_found()
    authorize(user, action, item)
    return item


def allowed_actions(user: CurrentUser, item: WorkItem) -> list[str]:
    actions: list[str] = []
    if can(user, Action.COMMENT, item):
        actions.append("comment")
    if item.status == Status.OPEN and item.assignee_id is None and can(user, Action.CLAIM, item):
        actions.append("claim")
    if item.status in ACTIVE_STATUSES:
        if can(user, Action.EDIT, item):
            actions.append("edit")
        if can(user, Action.CHANGE_PRIORITY, item):
            actions.append("change_priority")
        if item.status != Status.AWAITING_APPROVAL and can(user, Action.ASSIGN, item):
            actions.append("assign")
    for action, t in TRANSITIONS.items():
        if transition_applies(action, item) and can(user, t.permission, item):
            actions.append(action.value)
    return actions


def item_out(user: CurrentUser, item: WorkItem) -> ItemOut:
    out = ItemOut.model_validate(item)
    out.allowed_actions = allowed_actions(user, item)
    return out


# ---------------------------------------------------------------------------------------------------------------
# History and async side effects
# ---------------------------------------------------------------------------------------------------------------


def record_event(
    db: Session,
    item: WorkItem,
    actor: CurrentUser,
    kind: EventKind,
    from_value: Any = None,
    to_value: Any = None,
    comment_id: int | None = None,
) -> ActivityEvent:
    """Append a history event and queue its notifications, in the caller's transaction."""
    event = ActivityEvent(
        item_id=item.id, actor_id=actor.id, kind=kind, from_value=from_value, to_value=to_value, comment_id=comment_id
    )
    db.add(event)
    db.flush()
    db.add(OutboxMessage(event_type="activity", payload={"event_id": event.id, "item_id": item.id}))
    return event


def _user_ref(db: Session, user_id: int | None) -> dict | None:
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return {"id": user.id, "name": user.name} if user else None


# ---------------------------------------------------------------------------------------------------------------
# Optimistic concurrency
# ---------------------------------------------------------------------------------------------------------------


def _version_conflict(db: Session, user: CurrentUser, item: WorkItem, expected: int) -> AppError:
    db.refresh(item)
    return AppError(
        409,
        "version_conflict",
        "This item was changed by someone else. Review the latest version and try again.",
        {"expected_version": expected, "current": item_out(user, item)},
    )


def _guarded_update(db: Session, user: CurrentUser, item: WorkItem, expected_version: int, **values: Any) -> None:
    """UPDATE ... WHERE version = expected. If another write got there first, nothing is updated and we
    return 409 with the current item instead of silently overwriting it."""
    if item.version != expected_version:
        raise _version_conflict(db, user, item, expected_version)
    updated = db.execute(
        update(WorkItem)
        .where(WorkItem.id == item.id, WorkItem.version == expected_version)
        .values(**values, version=WorkItem.version + 1, updated_at=func.now())
        .returning(WorkItem.id)
        .execution_options(synchronize_session=False)
    ).first()
    if updated is None:
        raise _version_conflict(db, user, item, expected_version)
    db.refresh(item)


# ---------------------------------------------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------------------------------------------


def create_item(db: Session, user: CurrentUser, data: ItemCreate) -> ItemOut:
    authorize_create(user, data.team_id)
    item = WorkItem(
        team_id=data.team_id,
        type=data.type,
        title=data.title.strip(),
        description=data.description,
        priority=data.priority,
        status=Status.OPEN,
        created_by_id=user.id,
        requires_approval=data.requires_approval or data.type in APPROVAL_REQUIRED_TYPES,
        due_at=data.due_at,
    )
    db.add(item)
    db.flush()
    record_event(db, item, user, EventKind.CREATED, to_value={"status": item.status, "priority": item.priority})
    db.refresh(item)
    return item_out(user, item)


def update_item(db: Session, user: CurrentUser, item_id: int, expected_version: int, data: ItemUpdate) -> ItemOut:
    item = load_item(db, user, item_id)
    if item.status not in ACTIVE_STATUSES:
        raise AppError(409, "item_not_editable", f"Items in status {item.status} can't be edited")

    changes = data.model_dump(exclude_unset=True)
    for field in ("title", "priority"):
        if field in changes and changes[field] is None:
            raise AppError(422, "validation_error", f"{field} cannot be empty")
    changes = {k: v for k, v in changes.items() if getattr(item, k) != v}
    if not changes:
        return item_out(user, item)

    if "priority" in changes:
        authorize(user, Action.CHANGE_PRIORITY, item)
    if changes.keys() - {"priority"}:
        authorize(user, Action.EDIT, item)

    before = {k: getattr(item, k) for k in changes}
    _guarded_update(db, user, item, expected_version, **changes)

    if "priority" in changes:
        record_event(db, item, user, EventKind.PRIORITY_CHANGED, before["priority"], changes["priority"])
    edited = {k: v for k, v in changes.items() if k != "priority"}
    if edited:
        as_json = lambda d: {k: v.isoformat() if isinstance(v, datetime) else v for k, v in d.items()}  # noqa: E731
        record_event(db, item, user, EventKind.EDITED, as_json({k: before[k] for k in edited}), as_json(edited))
    return item_out(user, item)


def claim_item(db: Session, user: CurrentUser, item_id: int) -> ItemOut:
    """Take an unassigned OPEN item. Safe under races: the assignment is a single conditional UPDATE,
    so when several people claim at once exactly one row update succeeds."""
    item = load_item(db, user, item_id, Action.CLAIM)
    if item.assignee_id == user.id:
        return item_out(user, item)  # already yours: repeating the claim is harmless

    claimed = db.execute(
        update(WorkItem)
        .where(WorkItem.id == item.id, WorkItem.assignee_id.is_(None), WorkItem.status == Status.OPEN)
        .values(assignee_id=user.id, status=Status.IN_PROGRESS, version=WorkItem.version + 1, updated_at=func.now())
        .returning(WorkItem.id)
        .execution_options(synchronize_session=False)
    ).first()
    db.refresh(item)
    if claimed is None:
        if item.assignee_id == user.id:
            return item_out(user, item)
        if item.assignee_id is not None:
            raise AppError(
                409,
                "already_claimed",
                f"{item.assignee.name} already owns this item",
                {"current": item_out(user, item)},
            )
        raise AppError(409, "invalid_transition", f"Items in status {item.status} can't be claimed")

    record_event(db, item, user, EventKind.ASSIGNED, None, {"id": user.id, "name": user.name})
    record_event(db, item, user, EventKind.STATUS_CHANGED, Status.OPEN.value, Status.IN_PROGRESS.value)
    return item_out(user, item)


def assign_item(db: Session, user: CurrentUser, item_id: int, expected_version: int, assignee_id: int) -> ItemOut:
    item = load_item(db, user, item_id, Action.ASSIGN)
    if item.status not in (Status.OPEN, Status.IN_PROGRESS):
        raise AppError(409, "invalid_transition", f"Items in status {item.status} can't be reassigned")

    role = db.execute(
        select(Membership.role).where(Membership.user_id == assignee_id, Membership.team_id == item.team_id)
    ).scalar_one_or_none()
    if role is None or ROLE_RANK[Role(role)] < ROLE_RANK[Role.MEMBER]:
        raise AppError(422, "invalid_assignee", "Assignee must be a Member or Lead of the item's team")
    if item.assignee_id == assignee_id:
        return item_out(user, item)

    previous = _user_ref(db, item.assignee_id)
    _guarded_update(db, user, item, expected_version, assignee_id=assignee_id)
    record_event(db, item, user, EventKind.ASSIGNED, previous, _user_ref(db, assignee_id))
    return item_out(user, item)


def transition_item(
    db: Session, user: CurrentUser, item_id: int, expected_version: int, action: TransitionAction, comment: str | None
) -> ItemOut:
    item = load_item(db, user, item_id)
    t = TRANSITIONS[action]
    if not transition_applies(action, item):
        raise AppError(
            409,
            "invalid_transition",
            f"Can't {action.value} an item that is {item.status}"
            + (" (this item requires approval)" if action == TransitionAction.RESOLVE and item.requires_approval else ""),
            {"status": item.status, "allowed_actions": allowed_actions(user, item)},
        )
    authorize(user, t.permission, item)
    comment = (comment or "").strip() or None
    if t.comment_required and not comment:
        raise AppError(422, "comment_required", f"A comment explaining why is required to {action.value}")

    values: dict[str, Any] = {"status": t.target}
    if t.target == Status.RESOLVED:
        values["resolved_at"] = func.now()
    elif t.target in (Status.IN_PROGRESS, Status.OPEN):
        values["resolved_at"] = None
    if action == TransitionAction.RELEASE:
        values["assignee_id"] = None
    if action == TransitionAction.SUBMIT:
        values["submitted_by_id"] = user.id
    elif item.status == Status.AWAITING_APPROVAL:
        values["submitted_by_id"] = None
    previous_status = item.status
    previous_assignee = _user_ref(db, item.assignee_id)
    _guarded_update(db, user, item, expected_version, **values)

    comment_id = None
    if comment:
        row = Comment(item_id=item.id, author_id=user.id, body=comment)
        db.add(row)
        db.flush()
        comment_id = row.id
    record_event(db, item, user, EventKind.STATUS_CHANGED, previous_status, t.target.value, comment_id=comment_id)
    if action == TransitionAction.RELEASE:
        record_event(db, item, user, EventKind.ASSIGNED, previous_assignee, None)
    return item_out(user, item)


def add_comment(db: Session, user: CurrentUser, item_id: int, body: str) -> ActivityOut:
    """Comments don't bump the item's version: a comment shouldn't make someone's in-flight edit stale."""
    item = load_item(db, user, item_id, Action.COMMENT)
    row = Comment(item_id=item.id, author_id=user.id, body=body.strip())
    db.add(row)
    db.flush()
    db.execute(update(WorkItem).where(WorkItem.id == item.id).values(updated_at=func.now()))
    event = record_event(db, item, user, EventKind.COMMENTED, comment_id=row.id)
    db.refresh(event)
    return _activity_out(event)


# ---------------------------------------------------------------------------------------------------------------
# Lists, search and dashboard
# ---------------------------------------------------------------------------------------------------------------

SORTS = {
    # name: (columns, descending)
    "-updated_at": ((WorkItem.updated_at, WorkItem.id), True),
    "priority": ((WorkItem.priority, WorkItem.created_at, WorkItem.id), False),
}


def _encode_cursor(values: list[Any]) -> str:
    raw = json.dumps([v.isoformat() if isinstance(v, datetime) else v for v in values])
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str, columns) -> list[Any]:
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        assert isinstance(values, list) and len(values) == len(columns)
        return [datetime.fromisoformat(v) if c.type.python_type is datetime else v for v, c in zip(values, columns)]
    except Exception:
        raise AppError(400, "invalid_cursor", "Cursor is invalid; restart from the first page")


def _lead_team_ids(user: CurrentUser) -> list[int] | None:
    if user.is_admin:
        return None
    return [team_id for team_id, role in user.roles.items() if role == Role.LEAD]


def _apply_view(query: Select, user: CurrentUser, view: str | None) -> Select:
    if view is None:
        return query
    elif view == "assigned_to_me":
        return query.where(WorkItem.assignee_id == user.id, WorkItem.status.in_(ACTIVE_STATUSES))
    elif view == "unassigned_urgent":
        return query.where(
            WorkItem.assignee_id.is_(None), WorkItem.status == Status.OPEN, WorkItem.priority.in_(URGENT)
        )
    elif view == "awaiting_my_approval":
        query = query.where(
            WorkItem.status == Status.AWAITING_APPROVAL,
            WorkItem.assignee_id != user.id,
            WorkItem.submitted_by_id.is_distinct_from(user.id),
        )
        lead_teams = _lead_team_ids(user)
        return query if lead_teams is None else query.where(WorkItem.team_id.in_(lead_teams))
    else:
        raise AppError(400, "invalid_view", f"Unknown view {view!r}")


def _visible(query: Select, user: CurrentUser) -> Select:
    teams = visible_team_ids(user)
    return query if teams is None else query.where(WorkItem.team_id.in_(teams))


def list_items(
    db: Session,
    user: CurrentUser,
    *,
    view: str | None = None,
    team_id: int | None = None,
    status: list[Status] | None = None,
    priority: list[Priority] | None = None,
    type: list[str] | None = None,
    assignee: str | None = None,
    q: str | None = None,
    sort: str = "-updated_at",
    cursor: str | None = None,
    limit: int = 25,
) -> ItemPage:
    if sort not in SORTS:
        raise AppError(400, "invalid_sort", f"sort must be one of {sorted(SORTS)}")
    columns, descending = SORTS[sort]

    query = _apply_view(_visible(select(WorkItem), user), user, view)
    if team_id is not None:
        query = query.where(WorkItem.team_id == team_id)
    if status:
        query = query.where(WorkItem.status.in_(status))
    if priority:
        query = query.where(WorkItem.priority.in_(priority))
    if type:
        query = query.where(WorkItem.type.in_(type))
    if assignee == "me":
        query = query.where(WorkItem.assignee_id == user.id)
    elif assignee == "none":
        query = query.where(WorkItem.assignee_id.is_(None))
    elif assignee:
        if not assignee.isdigit():
            raise AppError(400, "invalid_assignee", "assignee must be 'me', 'none' or a user id")
        query = query.where(WorkItem.assignee_id == int(assignee))
    if q and q.strip():
        text_match = WorkItem.search_tsv.op("@@")(func.websearch_to_tsquery("english", q))
        number = q.strip().lstrip("#")
        query = query.where(or_(text_match, WorkItem.id == int(number)) if number.isdigit() else text_match)

    if cursor:
        after = _decode_cursor(cursor, columns)
        key = tuple_(*columns)
        query = query.where(key < tuple_(*after) if descending else key > tuple_(*after))
    order = [c.desc() for c in columns] if descending else list(columns)
    rows = db.execute(query.order_by(*order).limit(limit + 1)).unique().scalars().all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor([getattr(rows[-1], c.key) for c in columns]) if has_more else None
    return ItemPage(items=[ItemSummary.model_validate(r) for r in rows], next_cursor=next_cursor)


def similar_items(db: Session, user: CurrentUser, item_id: int, limit: int = 5) -> list[ItemSummary]:
    """Items whose title and description share words with this one, best match first.

    Uses the same full-text column as search: the item's own words become an OR query
    ('refund' | 'charg' | 'twice'), so any shared word is a match and ts_rank puts items sharing more words first.
    """
    item = load_item(db, user, item_id)
    words = db.execute(
        text("SELECT string_agg(quote_literal(lexeme), ' | ') FROM work_items, unnest(search_tsv) WHERE id = :id"),
        {"id": item.id},
    ).scalar()
    if not words:  # nothing but stop words, e.g. a title like "It is"
        return []
    # The words are already stemmed, so the 'simple' config uses them as they are.
    query_words = func.to_tsquery("simple", words)
    query = (
        _visible(select(WorkItem), user)
        .where(WorkItem.id != item.id, WorkItem.search_tsv.op("@@")(query_words))
        .order_by(func.ts_rank(WorkItem.search_tsv, query_words).desc(), WorkItem.id.desc())
        .limit(limit)
    )
    return [ItemSummary.model_validate(r) for r in db.execute(query).unique().scalars()]


def dashboard_counts(db: Session, user: CurrentUser) -> DashboardCounts:
    def count(view: str) -> int:
        query = _apply_view(_visible(select(func.count()).select_from(WorkItem), user), user, view)
        return db.execute(query).scalar_one()

    return DashboardCounts(
        assigned_to_me=count("assigned_to_me"),
        unassigned_urgent=count("unassigned_urgent"),
        awaiting_my_approval=count("awaiting_my_approval"),
    )


def _activity_out(event: ActivityEvent) -> ActivityOut:
    return ActivityOut(
        id=event.id,
        kind=event.kind,
        actor=UserRef.model_validate(event.actor),
        from_value=event.from_value,
        to_value=event.to_value,
        comment=event.comment.body if event.comment else None,
        created_at=event.created_at,
    )


def list_activity(
    db: Session, user: CurrentUser, item_id: int, cursor: int | None = None, limit: int = 50
) -> ActivityPage:
    load_item(db, user, item_id)
    query = select(ActivityEvent).where(ActivityEvent.item_id == item_id)
    if cursor is not None:
        query = query.where(ActivityEvent.id < cursor)
    rows = db.execute(query.order_by(ActivityEvent.id.desc()).limit(limit + 1)).unique().scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return ActivityPage(events=[_activity_out(e) for e in rows], next_cursor=str(rows[-1].id) if has_more else None)


def team_members(db: Session, user: CurrentUser, team_id: int) -> list[tuple[User, str]]:
    if not has_role(user, team_id, Role.VIEWER):
        raise not_found("Team")
    return db.execute(
        select(User, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.team_id == team_id)
        .order_by(User.name)
    ).all()

