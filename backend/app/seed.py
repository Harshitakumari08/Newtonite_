from __future__ import annotations
"""Seed demo teams, users and a realistic volume of work items.

    python -m app.seed            # seeds only if the database is empty
    python -m app.seed --items 50000

Every seeded user's password is "password".
"""

import argparse
import os
import random
try:
    from datetime import UTC, datetime, timedelta
except ImportError:
    from datetime import datetime, timedelta, timezone
    UTC = timezone.utc

from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import settings
from app.db import SessionLocal
from app.models import (
    APPROVAL_REQUIRED_TYPES,
    ActivityEvent,
    EventKind,
    ItemType,
    Membership,
    Priority,
    Role,
    Status,
    Team,
    User,
    WorkItem,
)

TEAMS = ["Support", "Payments", "Engineering", "Compliance"]

# (name, email prefix, is_admin, {team: role})
USERS = [
    ("Admin", "admin", True, {t: Role.LEAD for t in TEAMS}),
    ("Alice Lead", "alice", False, {"Support": Role.LEAD}),
    ("Bob Member", "bob", False, {"Support": Role.MEMBER, "Payments": Role.MEMBER}),
    ("Carol Member", "carol", False, {"Support": Role.MEMBER}),
    ("Priya Lead", "priya", False, {"Payments": Role.LEAD}),
    ("Dev Member", "dev", False, {"Payments": Role.MEMBER}),
    ("Erin Lead", "erin", False, {"Engineering": Role.LEAD}),
    ("Frank Member", "frank", False, {"Engineering": Role.MEMBER}),
    ("Dana Member", "dana", False, {"Engineering": Role.MEMBER, "Support": Role.MEMBER}),
    ("Grace Lead", "grace", False, {"Compliance": Role.LEAD}),
    ("Henry Member", "henry", False, {"Compliance": Role.MEMBER}),
    ("Victor Viewer", "victor", False, {"Support": Role.VIEWER, "Compliance": Role.VIEWER}),
]

TEAM_TYPES = {
    "Support": [ItemType.CUSTOMER_ISSUE, ItemType.TASK, ItemType.INCIDENT],
    "Payments": [ItemType.PAYMENT, ItemType.CUSTOMER_ISSUE, ItemType.TASK],
    "Engineering": [ItemType.INCIDENT, ItemType.TASK],
    "Compliance": [ItemType.COMPLIANCE, ItemType.TASK],
}

SUBJECTS = {
    ItemType.CUSTOMER_ISSUE: ["Customer can't log in", "Refund not received", "Invoice shows wrong amount",
                              "Account locked after password reset", "Export to CSV fails"],
    ItemType.INCIDENT: ["API latency spike in eu-west", "Checkout returning 500s", "Queue backlog growing",
                        "Database CPU at 95%", "Search results empty for some users"],
    ItemType.PAYMENT: ["Investigate duplicate charge", "Chargeback dispute", "Payout delayed to merchant",
                       "Bank transfer failed reconciliation", "Approve vendor payment"],
    ItemType.COMPLIANCE: ["GDPR data deletion request", "KYC document review", "Quarterly access review",
                          "Audit evidence for SOC 2", "Sanctions screening hit"],
    ItemType.TASK: ["Update runbook", "Rotate API credentials", "Onboard new hire", "Clean up stale dashboards",
                    "Review on-call schedule"],
}

STATUS_WEIGHTS = {
    Status.OPEN: 25, Status.IN_PROGRESS: 20, Status.AWAITING_APPROVAL: 5,
    Status.RESOLVED: 15, Status.CLOSED: 30, Status.CANCELLED: 5,
}
PRIORITY_WEIGHTS = {Priority.P1: 5, Priority.P2: 20, Priority.P3: 50, Priority.P4: 25}


def seed(db: Session, n_items: int, rng: random.Random) -> None:
    teams = {name: Team(name=name) for name in TEAMS}
    db.add_all(teams.values())
    demo_password = os.environ.get("DEMO_USER_PASSWORD")
    if not demo_password:
        if getattr(settings, "environment", "development").lower() == "production":
            raise RuntimeError("Refusing to seed demo accounts in production without DEMO_USER_PASSWORD set")
        demo_password = "password"
    password = hash_password(demo_password)
    users: dict[str, User] = {}
    for name, prefix, is_admin, roles in USERS:
        user = User(name=name, email=f"{prefix}@example.com", password_hash=password, is_admin=is_admin)
        db.add(user)
        users[prefix] = user
    db.flush()
    for name, prefix, _, roles in USERS:
        for team, role in roles.items():
            db.add(Membership(user_id=users[prefix].id, team_id=teams[team].id, role=role))
    db.flush()

    workers = {
        t: [users[p].id for _, p, _, roles in USERS if roles.get(t) in (Role.MEMBER, Role.LEAD)] for t in TEAMS
    }
    now = datetime.now(UTC)
    statuses, s_weights = zip(*STATUS_WEIGHTS.items())
    priorities, p_weights = zip(*PRIORITY_WEIGHTS.items())

    batch: list[dict] = []
    for i in range(n_items):
        team = rng.choice(TEAMS)
        item_type = rng.choice(TEAM_TYPES[team])
        status = rng.choices(statuses, s_weights)[0]
        requires_approval = item_type in APPROVAL_REQUIRED_TYPES
        if status == Status.AWAITING_APPROVAL and not requires_approval:
            status = Status.IN_PROGRESS
        created = now - timedelta(minutes=rng.randint(10, 60 * 24 * 180))
        updated = created + timedelta(minutes=rng.randint(0, int((now - created).total_seconds() // 60)))
        assignee = None
        if status != Status.OPEN or rng.random() < 0.3:
            assignee = rng.choice(workers[team])
        batch.append(
            {
                "team_id": teams[team].id,
                "type": item_type,
                "title": f"{rng.choice(SUBJECTS[item_type])} ({rng.randint(1000, 9999)})",
                "description": "Seeded demo item. Reported via the old spreadsheet workflow.",
                "priority": rng.choices(priorities, p_weights)[0],
                "status": status,
                "assignee_id": assignee,
                "created_by_id": rng.choice(workers[team]),
                "requires_approval": requires_approval,
                "due_at": created + timedelta(days=rng.randint(1, 30)) if rng.random() < 0.4 else None,
                "created_at": created,
                "updated_at": updated,
                "resolved_at": updated if status in (Status.RESOLVED, Status.CLOSED) else None,
            }
        )
        if len(batch) == 2000 or i == n_items - 1:
            ids = db.execute(insert(WorkItem).returning(WorkItem.id, WorkItem.created_by_id, WorkItem.created_at),
                             batch).all()
            db.execute(
                insert(ActivityEvent),
                [
                    {"item_id": item_id, "actor_id": creator, "kind": EventKind.CREATED, "created_at": created_at,
                     "to_value": {"status": "OPEN"}}
                    for item_id, creator, created_at in ids
                ],
            )
            batch.clear()
    db.commit()
    db.execute(text("ANALYZE"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    with SessionLocal() as db:
        if db.execute(select(func.count()).select_from(User)).scalar_one() > 0:
            print("Database already has users; skipping seed.")
            return
        seed(db, args.items, random.Random(args.seed))
        print(f"Seeded {len(TEAMS)} teams, {len(USERS)} users and {args.items} work items. Password: 'password'")


if __name__ == "__main__":
    main()
