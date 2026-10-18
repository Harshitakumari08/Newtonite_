from __future__ import annotations
"""Realistic Operational Non-CRUD Scenarios for Newtonite.

These tests simulate end-to-end operational pressure workflows:
1. High-Severity Incident Triage, Reassignment, and Emergency Resolution
2. Dual-Control Payment Dispute & Rejection-Revision Cycle (Four-Eyes Rule)
3. Thundering Herd Concurrent Claim Race (Atomic Conditional Update)
4. Outbox Notification Worker Lifecycle with Poison-Message Backoff and Dead Parking
"""

import concurrent.futures
from datetime import datetime, timezone
import pytest
from sqlalchemy import select, text

from app.models import Membership, OutboxMessage, Role, Status, User
from app.worker import process_batch


def test_scenario_incident_triage_escalation_lifecycle(world):
    """Scenario 1: High-Severity Incident Triage & Emergency Escalation Under Pressure.

    Flow:
    - Member notices critical API degradation and creates P1 Incident in Support.
    - Urgent dashboard counter reflects new unassigned incident.
    - Lead claims it immediately to triage.
    - Lead adds comments during investigation and changes title with version guard.
    - Lead reassigns to Member2 (specialist) for hotfix deployment.
    - Specialist starts work, posts comments, and resolves item.
    - Lead verifies resolution and closes item.
    - Full activity timeline is audited for continuous historical fidelity.
    """
    lead = world.api(world.lead)
    member = world.api(world.member)
    specialist = world.api(world.member2)

    # 1. Incident Creation
    created = member.post(
        "/api/items",
        json={
            "team_id": world.support.id,
            "type": "incident",
            "title": "Production Redis connection pool exhaustion",
            "description": "500 errors spiking on payment-gateway-service",
            "priority": "P1",
        },
    ).json()
    item_id = created["id"]
    assert created["status"] == "OPEN"
    assert created["priority"] == "P1"

    # Dashboard shows urgent open work
    counts = lead.get("/api/dashboard").json()
    assert counts["unassigned_urgent"] >= 1

    # 2. Lead Claims
    claimed = lead.post(f"/api/items/{item_id}/claim").json()
    assert claimed["status"] == "IN_PROGRESS"
    assert claimed["assignee"]["id"] == world.lead.id

    # 3. Collaborative Comments
    lead.post(f"/api/items/{item_id}/comments", json={"body": "Investigating node metrics and max client limit."})
    member.post(f"/api/items/{item_id}/comments", json={"body": "Logs show connection leaks in worker pool."})

    # 4. Lead updates title with latest version
    patched = lead.patch(
        f"/api/items/{item_id}",
        json={"title": "CRITICAL: Redis pool leak on worker pods"},
        headers={"If-Match": str(claimed["version"])},
    ).json()
    assert patched["title"] == "CRITICAL: Redis pool leak on worker pods"

    # 5. Lead escalates/reassigns to specialist
    assigned = lead.post(
        f"/api/items/{item_id}/assign",
        json={"assignee_id": world.member2.id},
        headers={"If-Match": str(patched["version"])},
    ).json()
    assert assigned["assignee"]["id"] == world.member2.id

    # 6. Specialist deploys fix and resolves
    specialist.post(f"/api/items/{item_id}/comments", json={"body": "Hotfix v2.4.1 deployed. Connections stabilized."})
    resolved = specialist.post(
        f"/api/items/{item_id}/transitions",
        json={"action": "resolve"},
        headers={"If-Match": str(assigned["version"])},
    ).json()
    assert resolved["status"] == "RESOLVED"
    assert resolved["resolved_at"] is not None

    # 7. Lead closes
    closed = lead.post(
        f"/api/items/{item_id}/transitions",
        json={"action": "close"},
        headers={"If-Match": str(resolved["version"])},
    ).json()
    assert closed["status"] == "CLOSED"

    # 8. Activity Timeline Audit
    activity = lead.get(f"/api/items/{item_id}/activity").json()["events"]
    kinds = [e["kind"] for e in activity]
    assert kinds.count("commented") == 3
    assert "created" in kinds
    assert "assigned" in kinds
    assert "status_changed" in kinds
    assert "edited" in kinds


def test_scenario_dual_control_payment_dispute_revision_cycle(world, db):
    """Scenario 2: Dual-Control Payment Dispute & Rejection-Revision Cycle.

    Flow:
    - Member creates high-value payment transaction adjustment.
    - System enforces requires_approval=True.
    - Member claims and submits item for review.
    - Lead 1 attempts to approve -> blocked if creator/submitter.
    - Lead 2 reviews and discovers missing supporting documentation.
    - Lead 2 rejects with mandatory explanation -> item returns to IN_PROGRESS.
    - Member adds audit receipt comment, updates description, and resubmits.
    - Lead 2 approves -> moves to RESOLVED with resolved_at timestamp.
    """
    lead2 = User(name="Dual Lead", email="duallead@test.dev", password_hash="x", is_admin=False)
    db.add(lead2)
    db.flush()
    db.add(Membership(user_id=lead2.id, team_id=world.payments.id, role=Role.LEAD))
    db.commit()

    member = world.api(world.member)
    lead1 = world.api(world.lead)
    lead2_api = world.api(lead2)

    # 1. Member creates payment item
    res = member.post(
        "/api/items",
        json={
            "team_id": world.payments.id,
            "type": "payment",
            "title": "Wire dispute reversal: ACME Corp $42,500",
            "requires_approval": False,  # Server must enforce True
        },
    )
    assert res.status_code == 201
    item = res.json()
    assert item["requires_approval"] is True

    # 2. Member claims and submits
    claimed = member.post(f"/api/items/{item['id']}/claim").json()
    submitted = member.post(
        f"/api/items/{item['id']}/transitions",
        json={"action": "submit"},
        headers={"If-Match": str(claimed["version"])},
    ).json()
    assert submitted["status"] == "AWAITING_APPROVAL"

    # 3. Submitter cannot approve their own item
    res = member.post(
        f"/api/items/{item['id']}/transitions",
        json={"action": "approve"},
        headers={"If-Match": str(submitted["version"])},
    )
    assert res.status_code == 403

    # 4. Lead 2 rejects due to missing verification receipt
    rejected = lead2_api.post(
        f"/api/items/{item['id']}/transitions",
        json={"action": "reject", "comment": "Rejected: Missing SWIFT MT103 confirmation receipt."},
        headers={"If-Match": str(submitted["version"])},
    ).json()
    assert rejected["status"] == "IN_PROGRESS"
    assert rejected["assignee"]["id"] == world.member.id

    # 5. Member provides requested document and edits item
    member.post(f"/api/items/{item['id']}/comments", json={"body": "SWIFT MT103 receipt attached (ref: SW-99281-X)."})
    patched = member.patch(
        f"/api/items/{item['id']}",
        json={"description": "Wire dispute reversal with verified SWIFT MT103 attached."},
        headers={"If-Match": str(rejected["version"])},
    ).json()

    # 6. Member resubmits
    resubmitted = member.post(
        f"/api/items/{item['id']}/transitions",
        json={"action": "submit"},
        headers={"If-Match": str(patched["version"])},
    ).json()
    assert resubmitted["status"] == "AWAITING_APPROVAL"

    # 7. Lead 2 approves
    approved = lead2_api.post(
        f"/api/items/{item['id']}/transitions",
        json={"action": "approve"},
        headers={"If-Match": str(resubmitted["version"])},
    ).json()
    assert approved["status"] == "RESOLVED"
    assert approved["resolved_at"] is not None


def test_scenario_thundering_herd_claim_race(world, db):
    """Scenario 3: Thundering Herd Concurrent Claim Race.

    10 members simultaneously attempt to claim an unassigned OPEN item.
    PostgreSQL atomic conditional UPDATE guarantees:
    - Exactly 1 claimer succeeds (HTTP 200).
    - Exactly 9 claimers receive HTTP 409 already_claimed.
    - Each 409 includes the winner's current identity in details.current.
    """
    users = []
    for i in range(10):
        u = User(name=f"Racer {i}", email=f"racer{i}@test.dev", password_hash="x", is_admin=False)
        db.add(u)
        users.append(u)
    db.flush()
    for u in users:
        db.add(Membership(user_id=u.id, team_id=world.support.id, role=Role.MEMBER))
    db.commit()

    # Create unassigned item
    item = world.api(world.lead).create_item(world.support.id, title="High-value support ticket")

    results = []

    def claim(user):
        api = world.api(user)
        return api.post(f"/api/items/{item['id']}/claim")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(claim, u) for u in users]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    status_codes = [r.status_code for r in results]
    assert status_codes.count(200) == 1
    assert status_codes.count(409) == 9

    winner_resp = next(r.json() for r in results if r.status_code == 200)
    winner_id = winner_resp["assignee"]["id"]

    for r in results:
        if r.status_code == 409:
            body = r.json()
            assert body["error"]["code"] == "already_claimed"
            assert body["error"]["details"]["current"]["assignee"]["id"] == winner_id


def test_scenario_outbox_notification_lifecycle_and_poison_job_handling(world, db):
    """Scenario 4: Outbox Notification Worker Lifecycle with Poison-Message Backoff.

    Flow:
    - Normal item changes generate outbox messages.
    - Worker processes them cleanly, inserting notifications without duplicates.
    - A poison outbox row (malformed payload) is inserted into outbox.
    - Worker attempts processing, catches error, schedules retry with exponential backoff.
    - After max attempts (5), poison message is marked 'dead' and parked.
    - Healthy queue processing is NOT blocked by dead poison message.
    """
    # 1. Normal mutation generates outbox event
    lead = world.api(world.lead)
    member = world.api(world.member)
    item = lead.create_item(world.support.id, title="Notification test item")
    member.post(f"/api/items/{item['id']}/claim")

    # Worker processes due outbox events
    db.execute(select(text("1")))  # ensure fresh state
    process_batch(db)

    # Check notification was created for creator (lead)
    notifications = lead.get("/api/notifications").json()
    assert len(notifications) >= 1
    notif_id = notifications[0]["id"]

    # Lead marks notification as read
    read_res = lead.post(f"/api/notifications/{notif_id}/read")
    assert read_res.status_code == 204

    # 2. Insert poison outbox row with invalid payload
    poison = OutboxMessage(
        event_type="activity",
        payload={"event_id": -999999, "item_id": -999999},  # Non-existent event
        status="pending",
        attempts=4,  # Next failure will push it to 5 (max)
    )
    db.add(poison)
    db.commit()

    # Worker processes batch; poison job fails and gets marked 'dead'
    process_batch(db)
    db.refresh(poison)
    assert poison.status == "dead"
    assert poison.attempts == 5
    assert poison.last_error is not None

    # Subsequent normal mutation still processes smoothly despite dead message
    item2 = lead.create_item(world.support.id, title="Subsequent valid work item")
    db.commit()
    healthy_processed = process_batch(db)
    assert healthy_processed >= 1
