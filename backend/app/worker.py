from __future__ import annotations
"""Outbox worker: turns committed activity events into in-app notifications.

Delivery is at-least-once. A job can run twice (e.g. the worker crashes after inserting notifications
but before marking the job done); that is harmless because notifications are unique per
(user_id, event_id) and inserted with ON CONFLICT DO NOTHING.

Several workers can run side by side: each claims a batch with FOR UPDATE SKIP LOCKED, so no two
workers process the same row at the same time. Failed jobs are retried with exponential backoff and
parked as 'dead' after `outbox_max_attempts`, so one poison message can't block the queue.

Run with:  python -m app.worker
"""

import logging
import signal
import time
try:
    from datetime import UTC, datetime, timedelta
except ImportError:
    from datetime import datetime, timedelta, timezone
    UTC = timezone.utc

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import (
    ActivityEvent,
    EventKind,
    IdempotencyKey,
    Membership,
    Notification,
    OutboxMessage,
    OutboxStatus,
    Role,
    Status,
    WorkItem,
)

log = logging.getLogger("worker")


def describe(event: ActivityEvent, item: WorkItem) -> str:
    who = event.actor.name
    if event.kind == EventKind.CREATED:
        return f"{who} created #{item.id} {item.title}"
    elif event.kind == EventKind.ASSIGNED:
        to = event.to_value["name"] if event.to_value else "nobody"
        return f"{who} assigned #{item.id} to {to}"
    elif event.kind == EventKind.STATUS_CHANGED:
        return f"{who} moved #{item.id} from {event.from_value} to {event.to_value}"
    elif event.kind == EventKind.PRIORITY_CHANGED:
        return f"{who} changed priority of #{item.id} to {event.to_value}"
    elif event.kind == EventKind.COMMENTED:
        return f"{who} commented on #{item.id}"
    else:
        return f"{who} updated #{item.id}"


def _team_leads(db: Session, team_id: int) -> set[int]:
    return set(
        db.execute(select(Membership.user_id).where(Membership.team_id == team_id, Membership.role == Role.LEAD))
        .scalars()
    )


def recipients(db: Session, event: ActivityEvent, item: WorkItem) -> set[int]:
    """Who hears about an event. The job may run after later changes, so a "created" event only goes to
    the team's leads (for P1s) rather than to whoever owns the item by the time the job runs."""
    if event.kind == EventKind.CREATED:
        return (_team_leads(db, item.team_id) if event.to_value.get("priority") == "P1" else set()) - {event.actor_id}
    users = {item.created_by_id}
    if item.assignee_id:
        users.add(item.assignee_id)
    if event.kind == EventKind.ASSIGNED and event.from_value:
        users.add(event.from_value["id"])  # tell the previous owner they lost the item
    if event.kind == EventKind.STATUS_CHANGED and event.to_value == Status.AWAITING_APPROVAL:
        users |= _team_leads(db, item.team_id)
    users.discard(event.actor_id)  # nobody needs to be told about their own action
    return users


def handle_activity(db: Session, payload: dict) -> None:
    event = db.get(ActivityEvent, payload["event_id"])
    item = db.get(WorkItem, payload["item_id"])
    if event is None or item is None:
        raise LookupError(f"event {payload.get('event_id')} or item {payload.get('item_id')} not found")
    message = describe(event, item)
    rows = [
        {"user_id": uid, "item_id": item.id, "event_id": event.id, "message": message}
        for uid in recipients(db, event, item)
    ]
    if rows:
        db.execute(insert(Notification).values(rows).on_conflict_do_nothing(constraint="uq_notifications_user_event"))


HANDLERS = {"activity": handle_activity}


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2**attempts, 300))


def process_batch(db: Session, batch_size: int = 50) -> int:
    """Process up to `batch_size` due jobs. Returns how many were picked up."""
    jobs = (
        db.execute(
            select(OutboxMessage)
            .where(OutboxMessage.status == OutboxStatus.PENDING, OutboxMessage.available_at <= datetime.now(UTC))
            .order_by(OutboxMessage.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for job in jobs:
        try:
            with db.begin_nested():  # a failing job rolls back only its own work
                HANDLERS[job.event_type](db, job.payload)
            job.status = OutboxStatus.DONE
        except Exception as exc:  # noqa: BLE001 - any failure is retried, then parked
            job.attempts += 1
            job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            if job.attempts >= settings.outbox_max_attempts:
                job.status = OutboxStatus.DEAD
                log.error("outbox job %s is dead after %s attempts: %s", job.id, job.attempts, job.last_error)
            else:
                job.available_at = datetime.now(UTC) + backoff(job.attempts)
                log.warning("outbox job %s failed (attempt %s): %s", job.id, job.attempts, job.last_error)
    db.commit()
    return len(jobs)


def purge_expired_idempotency_keys(db: Session) -> None:
    cutoff = datetime.now(UTC) - timedelta(hours=settings.idempotency_ttl_hours)
    db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff))
    db.commit()


def run(poll_seconds: float = 1.0) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("worker started")
    last_purge = 0.0
    while not stopping:
        with SessionLocal() as db:
            try:
                picked = process_batch(db)
                if time.monotonic() - last_purge > 3600:
                    purge_expired_idempotency_keys(db)
                    last_purge = time.monotonic()
            except Exception:
                log.exception("worker loop error")
                db.rollback()
                picked = 0
        if not picked:
            time.sleep(poll_seconds)
    log.info("worker stopped")


if __name__ == "__main__":
    run()
