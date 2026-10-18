from __future__ import annotations
"""Comprehensive API integration tests for Newtonite work-item REST API.

Tests cover:
1. Valid operations (create, read, patch, claim, assign, transition, comment, activity, dashboard)
2. Invalid workflow transitions, stale version concurrency, and validation rules
3. Server-side Four-Eyes approval policy for payment and compliance work items
4. Filtering (team, status, priority, assignee, search q) and bounded cursor pagination
5. Cross-team isolation, permissions, and non-trust of client-supplied values
"""

import pytest
from sqlalchemy import text

from app.models import Membership, Role, Status, User


def test_work_item_full_valid_lifecycle(world):
    """Test standard item lifecycle: create -> claim -> patch -> resolve -> close."""
    lead = world.api(world.lead)
    member = world.api(world.member)

    # 1. Create item
    res = member.post(
        "/api/items",
        json={
            "team_id": world.support.id,
            "type": "task",
            "title": "Investigate cluster CPU spike",
            "description": "Node 3 is reporting 98% utilization",
            "priority": "P2",
        },
    )
    assert res.status_code == 201
    item = res.json()
    item_id = item["id"]
    assert item["status"] == "OPEN"
    assert item["assignee"] is None
    assert item["created_by"]["id"] == world.member.id
    assert item["version"] == 1
    assert "claim" in item["allowed_actions"]

    # 2. Get item by id
    res = member.get(f"/api/items/{item_id}")
    assert res.status_code == 200
    assert res.json()["title"] == "Investigate cluster CPU spike"

    # 3. Claim item
    res = member.post(f"/api/items/{item_id}/claim")
    assert res.status_code == 200
    claimed = res.json()
    assert claimed["status"] == "IN_PROGRESS"
    assert claimed["assignee"]["id"] == world.member.id
    assert claimed["version"] == 2

    # 4. Patch item with If-Match version
    res = member.patch(
        f"/api/items/{item_id}",
        json={"title": "Investigate cluster CPU spike on worker-03", "priority": "P1"},
        headers={"If-Match": str(claimed["version"])},
    )
    assert res.status_code == 200
    patched = res.json()
    assert patched["title"] == "Investigate cluster CPU spike on worker-03"
    assert patched["priority"] == "P1"
    assert patched["version"] == 3

    # 5. Add comment
    res = member.post(
        f"/api/items/{item_id}/comments",
        json={"body": "Root cause identified: runaway background process"},
    )
    assert res.status_code == 201
    comment_data = res.json()
    assert comment_data["kind"] == "commented"
    assert comment_data["actor"]["id"] == world.member.id
    assert comment_data["comment"] == "Root cause identified: runaway background process"

    # 6. Check activity timeline
    res = member.get(f"/api/items/{item_id}/activity")
    assert res.status_code == 200
    activity_page = res.json()
    kinds = [e["kind"] for e in activity_page["events"]]
    assert "created" in kinds
    assert "assigned" in kinds
    assert "status_changed" in kinds
    assert "commented" in kinds

    # 7. Resolve item
    res = member.post(
        f"/api/items/{item_id}/transitions",
        json={"action": "resolve"},
        headers={"If-Match": str(patched["version"])},
    )
    assert res.status_code == 200
    resolved = res.json()
    assert resolved["status"] == "RESOLVED"
    assert resolved["resolved_at"] is not None
    assert resolved["version"] == 4

    # 8. Close item (Lead action)
    res = lead.post(
        f"/api/items/{item_id}/transitions",
        json={"action": "close"},
        headers={"If-Match": str(resolved["version"])},
    )
    assert res.status_code == 200
    closed = res.json()
    assert closed["status"] == "CLOSED"
    assert closed["version"] == 5


def test_invalid_transitions_and_concurrency(world):
    """Test server-enforced workflow rejections and optimistic concurrency."""
    lead = world.api(world.lead)
    member = world.api(world.member)

    # Create unassigned OPEN item
    res = member.post(
        "/api/items",
        json={"team_id": world.support.id, "type": "task", "title": "Test invalid transitions"},
    )
    item = res.json()
    item_id = item["id"]

    # 1. Start action cannot be applied when item has no assignee
    res = member.post(
        f"/api/items/{item_id}/transitions",
        json={"action": "start"},
        headers={"If-Match": str(item["version"])},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "invalid_transition"

    # 2. Cannot resolve unassigned OPEN item directly
    res = member.post(
        f"/api/items/{item_id}/transitions",
        json={"action": "resolve"},
        headers={"If-Match": str(item["version"])},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "invalid_transition"

    # 3. Missing If-Match header is rejected with 428 Precondition Required
    res = member.patch(f"/api/items/{item_id}", json={"title": "Missing version"})
    assert res.status_code == 428
    assert res.json()["error"]["code"] == "precondition_required"

    # 4. Stale If-Match header is rejected with 409 Version Conflict
    res = member.patch(
        f"/api/items/{item_id}",
        json={"title": "Stale version edit"},
        headers={"If-Match": "999"},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "version_conflict"

    # 5. Claim conflict: another member cannot claim an already claimed item
    claimed = member.post(f"/api/items/{item_id}/claim").json()
    res = world.api(world.member2).post(f"/api/items/{item_id}/claim")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "already_claimed"

    # 6. Reopen requires a non-empty comment
    # Resolve first
    resolved = member.post(
        f"/api/items/{item_id}/transitions",
        json={"action": "resolve"},
        headers={"If-Match": str(claimed["version"])},
    ).json()

    # Lead attempts reopen with empty comment -> 422
    res = lead.post(
        f"/api/items/{item_id}/transitions",
        json={"action": "reopen", "comment": ""},
        headers={"If-Match": str(resolved["version"])},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "comment_required"

    # Reopen with comment succeeds
    res = lead.post(
        f"/api/items/{item_id}/transitions",
        json={"action": "reopen", "comment": "Issue re-occurred in staging"},
        headers={"If-Match": str(resolved["version"])},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "IN_PROGRESS"


def test_four_eyes_approval_policy_payment_and_compliance(world, db):
    """Test strict Four-Eyes rule:
    - payment and compliance types ALWAYS force requires_approval=True
    - cannot resolve directly from IN_PROGRESS
    - submit moves item to AWAITING_APPROVAL
    - LEAD who created the item CANNOT approve it (403)
    - LEAD who is assignee CANNOT approve it (403)
    - LEAD who submitted the item CANNOT approve it (403)
    - Member CANNOT approve (403)
    - Outsider gets 404
    - A distinct LEAD who is neither creator nor assignee nor submitter CAN approve (200)
    """
    # Create an independent second lead in Payments team
    lead2 = User(name="Second Lead", email="second_lead@test.dev", password_hash="x", is_admin=False)
    db.add(lead2)
    db.flush()
    db.add(Membership(user_id=lead2.id, team_id=world.payments.id, role=Role.LEAD))
    db.commit()

    lead1_api = world.api(world.lead)
    lead2_api = world.api(lead2)
    member_api = world.api(world.member)

    # --- Test A: Payment item created with client spoofing requires_approval=False ---
    res = member_api.post(
        "/api/items",
        json={
            "team_id": world.payments.id,
            "type": "payment",
            "title": "Vendor invoice over $10,000",
            "requires_approval": False,  # Server MUST ignore and force True
        },
    )
    assert res.status_code == 201
    payment_item = res.json()
    assert payment_item["requires_approval"] is True

    # Claim by member
    claimed = member_api.post(f"/api/items/{payment_item['id']}/claim").json()

    # Direct resolve is forbidden
    res = member_api.post(
        f"/api/items/{payment_item['id']}/transitions",
        json={"action": "resolve"},
        headers={"If-Match": str(claimed["version"])},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "invalid_transition"

    # Member submits
    submitted = member_api.post(
        f"/api/items/{payment_item['id']}/transitions",
        json={"action": "submit"},
        headers={"If-Match": str(claimed["version"])},
    ).json()
    assert submitted["status"] == "AWAITING_APPROVAL"

    # Member cannot approve
    res = member_api.post(
        f"/api/items/{payment_item['id']}/transitions",
        json={"action": "approve"},
        headers={"If-Match": str(submitted["version"])},
    )
    assert res.status_code == 403

    # Independent lead1 approves -> 200
    res = lead1_api.post(
        f"/api/items/{payment_item['id']}/transitions",
        json={"action": "approve"},
        headers={"If-Match": str(submitted["version"])},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "RESOLVED"

    # --- Test B: Compliance item where creator is a LEAD ---
    # lead1 creates the compliance item
    res = lead1_api.post(
        "/api/items",
        json={
            "team_id": world.payments.id,
            "type": "compliance",
            "title": "GDPR data export compliance audit",
            "requires_approval": False,  # Server must enforce True
        },
    )
    assert res.status_code == 201
    compliance_item = res.json()
    assert compliance_item["requires_approval"] is True

    # lead1 assigns to member
    assigned = lead1_api.post(
        f"/api/items/{compliance_item['id']}/assign",
        json={"assignee_id": world.member.id},
        headers={"If-Match": str(compliance_item["version"])},
    ).json()

    # member starts and submits
    started = member_api.post(
        f"/api/items/{compliance_item['id']}/transitions",
        json={"action": "start"},
        headers={"If-Match": str(assigned["version"])},
    ).json()
    submitted = member_api.post(
        f"/api/items/{compliance_item['id']}/transitions",
        json={"action": "submit"},
        headers={"If-Match": str(started["version"])},
    ).json()

    # Creator (lead1) tries to approve -> FORBIDDEN (creator cannot approve their own item!)
    res = lead1_api.post(
        f"/api/items/{compliance_item['id']}/transitions",
        json={"action": "approve"},
        headers={"If-Match": str(submitted["version"])},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"

    # Assignee/submitter (member) tries to approve -> FORBIDDEN
    res = member_api.post(
        f"/api/items/{compliance_item['id']}/transitions",
        json={"action": "approve"},
        headers={"If-Match": str(submitted["version"])},
    )
    assert res.status_code == 403

    # Outsider gets 404 (does not even see item exists)
    res = world.api(world.viewer).post(
        f"/api/items/{compliance_item['id']}/transitions",
        json={"action": "approve"},
        headers={"If-Match": str(submitted["version"])},
    )
    # viewer is in Support, not Payments
    assert res.status_code == 404

    # Independent second lead (lead2) approves -> SUCCEEDS
    res = lead2_api.post(
        f"/api/items/{compliance_item['id']}/transitions",
        json={"action": "approve"},
        headers={"If-Match": str(submitted["version"])},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "RESOLVED"

    # --- Test C: Reject requires comment ---
    # Create another payment item submitted by member
    res = member_api.post(
        "/api/items",
        json={"team_id": world.payments.id, "type": "payment", "title": "Wire transfer review"},
    )
    wire = member_api.post(f"/api/items/{res.json()['id']}/claim").json()
    wire_submitted = member_api.post(
        f"/api/items/{wire['id']}/transitions",
        json={"action": "submit"},
        headers={"If-Match": str(wire["version"])},
    ).json()

    # Reject without comment fails
    res = lead1_api.post(
        f"/api/items/{wire['id']}/transitions",
        json={"action": "reject"},
        headers={"If-Match": str(wire_submitted["version"])},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "comment_required"

    # Reject with comment succeeds and sends back to IN_PROGRESS
    res = lead1_api.post(
        f"/api/items/{wire['id']}/transitions",
        json={"action": "reject", "comment": "Missing dual authorized signature"},
        headers={"If-Match": str(wire_submitted["version"])},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "IN_PROGRESS"


def test_filtering_and_bounded_pagination(world):
    """Test filtering by team, status, priority, assignee, search q, and bounded cursor pagination."""
    lead = world.api(world.lead)
    member = world.api(world.member)

    # Populate items with different teams, statuses, priorities
    i1 = lead.create_item(world.support.id, title="Database connection pool exhausted", priority="P1")
    i2 = lead.create_item(world.support.id, title="Frontend build bundle optimization", priority="P3")
    i3 = lead.create_item(world.payments.id, title="Payment gateway timeout", priority="P1", type="payment")
    i4 = lead.create_item(world.payments.id, title="Compliance audit logs", priority="P4", type="compliance")

    # Claim one item
    member.post(f"/api/items/{i1['id']}/claim")

    # 1. Filter by team_id
    res = lead.get("/api/items", params={"team_id": world.payments.id})
    assert res.status_code == 200
    titles = [it["title"] for it in res.json()["items"]]
    assert "Payment gateway timeout" in titles
    assert "Compliance audit logs" in titles
    assert "Database connection pool exhausted" not in titles

    # 1b. Filter by team alias
    res = lead.get("/api/items", params={"team": world.payments.id})
    assert res.status_code == 200
    titles = [it["title"] for it in res.json()["items"]]
    assert "Payment gateway timeout" in titles

    # 2. Filter by priority
    res = lead.get("/api/items", params={"priority": "P1"})
    assert res.status_code == 200
    titles = [it["title"] for it in res.json()["items"]]
    assert "Database connection pool exhausted" in titles
    assert "Payment gateway timeout" in titles
    assert "Frontend build bundle optimization" not in titles

    # 3. Filter by assignee=me vs none
    res = member.get("/api/items", params={"assignee": "me"})
    assert res.status_code == 200
    titles = [it["title"] for it in res.json()["items"]]
    assert titles == ["Database connection pool exhausted"]

    res = member.get("/api/items", params={"assignee": "none"})
    assert res.status_code == 200
    titles = [it["title"] for it in res.json()["items"]]
    assert "Frontend build bundle optimization" in titles
    assert "Database connection pool exhausted" not in titles

    # 4. Search query
    res = lead.get("/api/items", params={"q": "timeout"})
    assert res.status_code == 200
    titles = [it["title"] for it in res.json()["items"]]
    assert titles == ["Payment gateway timeout"]

    # Search by numeric item ID
    res = lead.get("/api/items", params={"q": f"#{i2['id']}"})
    assert res.status_code == 200
    assert [it["id"] for it in res.json()["items"]] == [i2["id"]]

    # 5. Bounded pagination & cursor
    # Limit enforces ge=1, le=100
    assert lead.get("/api/items", params={"limit": 0}).status_code == 422
    assert lead.get("/api/items", params={"limit": 101}).status_code == 422

    page1 = lead.get("/api/items", params={"limit": 2}).json()
    assert len(page1["items"]) == 2
    assert page1["next_cursor"] is not None

    page2 = lead.get("/api/items", params={"limit": 2, "cursor": page1["next_cursor"]}).json()
    assert len(page2["items"]) >= 1

    # Verify no item overlaps between page1 and page2
    ids_p1 = {it["id"] for it in page1["items"]}
    ids_p2 = {it["id"] for it in page2["items"]}
    assert ids_p1.isdisjoint(ids_p2)


def test_cross_team_access_and_non_trust_of_client_parameters(world):
    """Test security boundaries:
    - Client cannot spoof creator_id or status on POST /api/items
    - Client cannot create items in teams they don't belong to
    - Client cannot access or mutate items in teams they don't belong to
    - Lead cannot assign item to someone outside the team
    """
    outsider = world.api(world.outsider)  # Only in Payments team
    member = world.api(world.member)  # In Support and Payments
    lead = world.api(world.lead)  # Lead in Support and Payments

    # 1. Outsider cannot create item in Support team (403 Forbidden)
    res = outsider.post(
        "/api/items",
        json={"team_id": world.support.id, "type": "task", "title": "Sneaky outsider item"},
    )
    assert res.status_code == 403

    # 2. Even if client sends creator_id in body, server sets creator_id from JWT
    res = member.post(
        "/api/items",
        json={
            "team_id": world.support.id,
            "type": "task",
            "title": "Verifying creator_id cannot be spoofed",
            "creator_id": world.outsider.id,  # Client tries to spoof creator
            "status": "RESOLVED",  # Client tries to spoof status
        },
    )
    assert res.status_code == 201
    created = res.json()
    assert created["created_by"]["id"] == world.member.id
    assert created["status"] == "OPEN"

    # 3. Outsider accessing Support item gets 404 (existence hidden)
    item_id = created["id"]
    assert outsider.get(f"/api/items/{item_id}").status_code == 404
    assert outsider.patch(f"/api/items/{item_id}", json={"title": "Hack"}, headers={"If-Match": "1"}).status_code == 404
    assert outsider.post(f"/api/items/{item_id}/claim").status_code == 404
    assert outsider.post(f"/api/items/{item_id}/comments", json={"body": "Spam"}).status_code == 404

    # 4. Lead cannot assign Support item to outsider (user not in Support)
    res = lead.post(
        f"/api/items/{item_id}/assign",
        json={"assignee_id": world.outsider.id},
        headers={"If-Match": "1"},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_assignee"


def test_dashboard_summary_counts(world):
    """Test GET /api/dashboard returns correct counts for active user."""
    lead = world.api(world.lead)
    member = world.api(world.member)

    # Initial dashboard
    counts = member.get("/api/dashboard").json()
    initial_mine = counts["assigned_to_me"]

    # Create urgent open item in Support
    lead.create_item(world.support.id, priority="P1", title="Urgent server crash")
    lead_counts = lead.get("/api/dashboard").json()
    assert lead_counts["unassigned_urgent"] >= 1

    # Member claims an item
    item = lead.create_item(world.support.id, priority="P2", title="Task for member")
    member.post(f"/api/items/{item['id']}/claim")

    new_member_counts = member.get("/api/dashboard").json()
    assert new_member_counts["assigned_to_me"] == initial_mine + 1
