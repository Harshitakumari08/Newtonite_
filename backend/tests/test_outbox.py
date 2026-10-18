from __future__ import annotations
try:
    from datetime import UTC, datetime, timedelta
except ImportError:
    from datetime import datetime, timedelta, timezone
    UTC = timezone.utc

from sqlalchemy import select, update
from starlette.testclient import TestClient

from app.main import app
from app import worker
from app.models import Notification, OutboxMessage, OutboxStatus


def run_worker(db):
    db.execute(update(OutboxMessage).values(available_at=datetime.now(UTC) - timedelta(seconds=1)))
    db.commit()
    while worker.process_batch(db):
        pass


def test_claim_notifies_the_creator_not_the_actor(world, db):
    item = world.api(world.lead).create_item(world.support.id)
    world.api(world.member).post(f"/api/items/{item['id']}/claim")
    run_worker(db)
    notified = {n.user_id for n in db.execute(select(Notification)).scalars()}
    assert notified == {world.lead.id}


def test_submitting_for_approval_notifies_team_leads(world, db):
    member = world.api(world.member)
    item = member.create_item(world.payments.id, type="payment", title="Refund")
    claimed = member.post(f"/api/items/{item['id']}/claim").json()
    member.transition(claimed, "submit")
    run_worker(db)
    lead_messages = [n.message for n in db.execute(select(Notification).where(Notification.user_id == world.lead.id)).scalars()]
    assert any("AWAITING_APPROVAL" in m for m in lead_messages)


def test_running_a_job_twice_does_not_duplicate_notifications(world, db):
    item = world.api(world.lead).create_item(world.support.id)
    world.api(world.member).post(f"/api/items/{item['id']}/claim")
    run_worker(db)
    first = db.execute(select(Notification)).scalars().all()

    # Simulate a crash after the side effect but before the job was marked done.
    db.execute(update(OutboxMessage).values(status=OutboxStatus.PENDING))
    db.commit()
    run_worker(db)
    assert len(db.execute(select(Notification)).scalars().all()) == len(first)


def test_failing_jobs_back_off_then_go_dead_without_blocking_others(world, db, monkeypatch):
    item = world.api(world.lead).create_item(world.support.id)
    good_id = db.execute(select(OutboxMessage.id)).scalar_one()
    poison = OutboxMessage(event_type="activity", payload={"event_id": 999_999, "item_id": item["id"]})
    db.add(poison)
    db.commit()
    monkeypatch.setattr(worker.settings, "outbox_max_attempts", 3)

    for _ in range(3):
        run_worker(db)

    db.expire_all()
    poison = db.get(OutboxMessage, poison.id)
    assert poison.status == OutboxStatus.DEAD
    assert poison.attempts == 3
    assert "not found" in poison.last_error
    assert db.get(OutboxMessage, good_id).status == OutboxStatus.DONE


def test_failed_job_is_scheduled_for_later(world, db):
    world.api(world.lead).create_item(world.support.id)
    poison = OutboxMessage(event_type="activity", payload={"event_id": 999_999, "item_id": 999_999})
    db.add(poison)
    db.commit()
    run_worker(db)
    db.expire_all()
    poison = db.get(OutboxMessage, poison.id)
    assert poison.status == OutboxStatus.PENDING
    assert poison.available_at > datetime.now(UTC)


def test_mark_all_notifications_read(world, db):
    # Create two items as Lead; Member claims both
    item1 = world.api(world.lead).create_item(world.support.id)
    item2 = world.api(world.lead).create_item(world.support.id)
    world.api(world.member).post(f"/api/items/{item1['id']}/claim")
    world.api(world.member).post(f"/api/items/{item2['id']}/claim")
    run_worker(db)

    # Lead checks unread notifications
    res = world.api(world.lead).get("/api/notifications?unread_only=true")
    assert res.status_code == 200
    assert len(res.json()) >= 2

    # Lead marks all as read
    read_all_res = world.api(world.lead).post("/api/notifications/read-all")
    assert read_all_res.status_code == 204

    # Now unread notifications are 0
    res_after = world.api(world.lead).get("/api/notifications?unread_only=true")
    assert res_after.status_code == 200
    assert len(res_after.json()) == 0


def test_mark_all_notifications_read_comprehensive_matrix(world, db):
    # 1. Unauthenticated or invalid token requests return 401
    unauth_client = TestClient(app)
    res_no_token = unauth_client.post("/api/notifications/read-all")
    assert res_no_token.status_code == 401
    res_bad_token = unauth_client.post(
        "/api/notifications/read-all", headers={"Authorization": "Bearer bad-jwt-token"}
    )
    assert res_bad_token.status_code == 401

    # 2. Setup: Lead has 3 items, Member claims all 3 => Lead gets 3 notifications
    item1 = world.api(world.lead).create_item(world.support.id)
    item2 = world.api(world.lead).create_item(world.support.id)
    item3 = world.api(world.lead).create_item(world.support.id)

    # Member also creates an item and Lead claims it => Member gets 1 notification
    member_item = world.api(world.member).create_item(world.support.id)

    world.api(world.member).post(f"/api/items/{item1['id']}/claim")
    world.api(world.member).post(f"/api/items/{item2['id']}/claim")
    world.api(world.member).post(f"/api/items/{item3['id']}/claim")
    world.api(world.lead).post(f"/api/items/{member_item['id']}/claim")

    run_worker(db)

    # Lead has unread notifications
    lead_unread = world.api(world.lead).get("/api/notifications?unread_only=true").json()
    assert len(lead_unread) >= 3

    # Member has unread notifications
    member_unread = world.api(world.member).get("/api/notifications?unread_only=true").json()
    assert len(member_unread) >= 1

    # Mark 1 notification as read individually for Lead
    first_notif_id = lead_unread[0]["id"]
    single_read_res = world.api(world.lead).post(f"/api/notifications/{first_notif_id}/read")
    assert single_read_res.status_code == 204

    # Lead unread count decreased by 1
    lead_unread_after_single = world.api(world.lead).get("/api/notifications?unread_only=true").json()
    assert len(lead_unread_after_single) == len(lead_unread) - 1

    # Now Lead marks all remaining as read
    read_all_res = world.api(world.lead).post("/api/notifications/read-all")
    assert read_all_res.status_code == 204

    # Lead unread count is now exactly 0
    assert len(world.api(world.lead).get("/api/notifications?unread_only=true").json()) == 0

    # All Lead notifications are now marked with read_at
    lead_all = world.api(world.lead).get("/api/notifications?unread_only=false").json()
    assert len(lead_all) >= 3
    assert all(n["read_at"] is not None for n in lead_all)

    # 3. Repeat requests on already-read notifications return 204 without error
    repeat1 = world.api(world.lead).post("/api/notifications/read-all")
    assert repeat1.status_code == 204
    repeat2 = world.api(world.lead).post("/api/notifications/read-all")
    assert repeat2.status_code == 204

    # 4. Multi-team / User isolation: Member's unread notifications are completely unaffected
    member_unread_after = world.api(world.member).get("/api/notifications?unread_only=true").json()
    assert len(member_unread_after) == len(member_unread)
    assert all(n["read_at"] is None for n in member_unread_after)
