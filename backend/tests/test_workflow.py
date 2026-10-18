from __future__ import annotations
from sqlalchemy import select

from app.models import ActivityEvent, OutboxMessage


def claimed_payment(world):
    """A payment item (always requires approval) claimed by `member`."""
    item = world.api(world.member).create_item(world.payments.id, type="payment", title="Duplicate charge")
    assert item["requires_approval"] is True
    return world.api(world.member).post(f"/api/items/{item['id']}/claim").json()


def test_items_needing_approval_cannot_skip_it(world):
    item = claimed_payment(world)
    response = world.api(world.member).transition(item, "resolve")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"


def test_full_approval_path(world):
    member, lead = world.api(world.member), world.api(world.lead)
    item = claimed_payment(world)
    submitted = member.transition(item, "submit")
    assert submitted.status_code == 200 and submitted.json()["status"] == "AWAITING_APPROVAL"

    approved = lead.transition(submitted.json(), "approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "RESOLVED"
    assert approved.json()["resolved_at"] is not None

    closed = lead.transition(approved.json(), "close")
    assert closed.json()["status"] == "CLOSED"
    # Terminal: nothing else is allowed.
    assert lead.transition(closed.json(), "reopen", comment="again").status_code == 409


def test_members_cannot_approve(world):
    item = claimed_payment(world)
    submitted = world.api(world.member).transition(item, "submit").json()
    # `outsider` is a Member of Payments: same team, wrong role.
    assert world.api(world.outsider).transition(submitted, "approve").status_code == 403


def test_leads_cannot_approve_their_own_work(world):
    lead = world.api(world.lead)
    item = lead.create_item(world.payments.id, type="payment", title="Vendor payment")
    item = lead.post(f"/api/items/{item['id']}/claim").json()
    submitted = lead.transition(item, "submit").json()
    response = lead.transition(submitted, "approve")
    assert response.status_code == 403  # four-eyes rule


def test_a_lead_who_submits_someone_elses_item_cannot_approve_it(world):
    item = claimed_payment(world)  # owned by `member`
    lead = world.api(world.lead)
    submitted = lead.transition(item, "submit")  # Leads may act on any item in their team...
    assert submitted.status_code == 200
    assert "approve" not in submitted.json()["allowed_actions"]
    assert lead.transition(submitted.json(), "approve").status_code == 403  # ...but not sign off their own submission
    assert lead.get("/api/items", params={"view": "awaiting_my_approval"}).json()["items"] == []
    # Another Lead can.
    assert world.api(world.admin).transition(submitted.json(), "approve").status_code == 200


def test_creator_cannot_approve_even_if_lead_and_item_submitted_by_another_member(world):
    lead, member, admin = world.api(world.lead), world.api(world.member), world.api(world.admin)
    # Lead creates an item requiring approval (payment)
    item = lead.create_item(world.payments.id, type="payment", title="Vendor refund audit")
    assert item["requires_approval"] is True

    # Member claims the item and works on it
    claimed = member.post(f"/api/items/{item['id']}/claim").json()
    assert claimed["assignee"]["id"] == world.member.id

    # Member submits it for approval
    submitted = member.transition(claimed, "submit").json()
    assert submitted["status"] == "AWAITING_APPROVAL"

    # The Lead who created the item attempts to approve it -> Forbidden by four-eyes principle!
    assert "approve" not in submitted["allowed_actions"]
    assert lead.transition(submitted, "approve").status_code == 403

    # An independent administrator (or another Lead not involved in creation/submission) can approve
    approved = admin.transition(submitted, "approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "RESOLVED"


def test_reject_requires_a_reason_and_returns_work_to_the_assignee(world):
    member, lead = world.api(world.member), world.api(world.lead)
    submitted = member.transition(claimed_payment(world), "submit").json()
    assert lead.transition(submitted, "reject").status_code == 422
    rejected = lead.transition(submitted, "reject", comment="Need the bank reference")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "IN_PROGRESS"
    assert rejected.json()["assignee"]["id"] == world.member.id


def test_transitions_check_the_version(world):
    member = world.api(world.member)
    item = member.create_item(world.support.id)
    claimed = member.post(f"/api/items/{item['id']}/claim").json()
    stale = member.transition(claimed, "resolve", version=claimed["version"] - 1)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"


def test_release_puts_the_item_back_in_the_queue(world):
    member = world.api(world.member)
    item = member.create_item(world.support.id)
    claimed = member.post(f"/api/items/{item['id']}/claim").json()
    released = member.transition(claimed, "release").json()
    assert released["status"] == "OPEN" and released["assignee"] is None
    assert world.api(world.member2).post(f"/api/items/{item['id']}/claim").status_code == 200


def test_every_change_is_recorded_with_its_outbox_job(world, db):
    member, lead = world.api(world.member), world.api(world.lead)
    item = member.create_item(world.support.id)
    claimed = member.post(f"/api/items/{item['id']}/claim").json()
    changed = lead.patch(f"/api/items/{item['id']}", json={"priority": "P1"},
                         headers={"If-Match": str(claimed["version"])}).json()
    member.transition(changed, "resolve")
    member.post(f"/api/items/{item['id']}/comments", json={"body": "Fixed by restarting the job"})

    timeline = member.get(f"/api/items/{item['id']}/activity").json()["events"]
    kinds = [e["kind"] for e in reversed(timeline)]
    assert kinds == ["created", "assigned", "status_changed", "priority_changed", "status_changed", "commented"]
    priority_event = next(e for e in timeline if e["kind"] == "priority_changed")
    assert (priority_event["from_value"], priority_event["to_value"]) == ("P2", "P1")
    assert priority_event["actor"]["id"] == world.lead.id
    assert timeline[0]["comment"] == "Fixed by restarting the job"

    event_ids = set(db.execute(select(ActivityEvent.id)).scalars())
    queued = {m.payload["event_id"] for m in db.execute(select(OutboxMessage)).scalars()}
    assert queued == event_ids  # every history event has exactly the async job that announces it


def test_failed_transition_leaves_no_trace(world, db):
    """History is written in the same transaction as the change, so a rejected change records nothing."""
    member = world.api(world.member)
    item = claimed_payment(world)
    before = len(db.execute(select(ActivityEvent)).scalars().all())
    assert member.transition(item, "resolve").status_code == 409
    after = len(db.execute(select(ActivityEvent)).scalars().all())
    assert after == before
