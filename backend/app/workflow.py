from __future__ import annotations
"""Server-enforced workflow. Any (status, action) pair not listed here is rejected."""

from dataclasses import dataclass
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum
    class StrEnum(str, Enum):
        pass

from app.models import Status, WorkItem
from app.permissions import Action


class TransitionAction(StrEnum):
    START = "start"
    RELEASE = "release"
    SUBMIT = "submit"
    RESOLVE = "resolve"
    APPROVE = "approve"
    REJECT = "reject"
    CLOSE = "close"
    REOPEN = "reopen"
    CANCEL = "cancel"


@dataclass(frozen=True)
class Transition:
    sources: tuple[Status, ...]
    target: Status
    permission: Action
    comment_required: bool = False
    # None = always allowed; True/False = only when item.requires_approval matches.
    needs_approval_flag: bool | None = None


TRANSITIONS: dict[TransitionAction, Transition] = {
    # OPEN -> IN_PROGRESS for an item a Lead has already assigned (self-claim is POST /claim).
    TransitionAction.START: Transition((Status.OPEN,), Status.IN_PROGRESS, Action.WORK),
    # Give the item back to the queue.
    TransitionAction.RELEASE: Transition((Status.IN_PROGRESS,), Status.OPEN, Action.WORK),
    TransitionAction.SUBMIT: Transition(
        (Status.IN_PROGRESS,), Status.AWAITING_APPROVAL, Action.WORK, needs_approval_flag=True
    ),
    TransitionAction.RESOLVE: Transition(
        (Status.IN_PROGRESS,), Status.RESOLVED, Action.WORK, needs_approval_flag=False
    ),
    TransitionAction.APPROVE: Transition((Status.AWAITING_APPROVAL,), Status.RESOLVED, Action.APPROVE),
    TransitionAction.REJECT: Transition(
        (Status.AWAITING_APPROVAL,), Status.IN_PROGRESS, Action.APPROVE, comment_required=True
    ),
    TransitionAction.CLOSE: Transition((Status.RESOLVED,), Status.CLOSED, Action.MANAGE),
    TransitionAction.REOPEN: Transition((Status.RESOLVED,), Status.IN_PROGRESS, Action.MANAGE, comment_required=True),
    TransitionAction.CANCEL: Transition(
        (Status.OPEN, Status.IN_PROGRESS, Status.AWAITING_APPROVAL), Status.CANCELLED, Action.MANAGE
    ),
}


def transition_applies(action: TransitionAction, item: WorkItem) -> bool:
    """True when the item's current state allows this action (permissions are checked separately)."""
    t = TRANSITIONS[action]
    if item.status not in t.sources:
        return False
    if t.needs_approval_flag is not None and item.requires_approval != t.needs_approval_flag:
        return False
    if action == TransitionAction.START and item.assignee_id is None:
        return False
    return True
