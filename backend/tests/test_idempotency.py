from __future__ import annotations
from sqlalchemy import func, select

from app.models import Comment, WorkItem
from tests.test_concurrency import run_concurrently


def count(db, model):
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_retried_create_returns_the_same_item_and_creates_one(world, db):
    api = world.api(world.member)
    body = {"team_id": world.support.id, "type": "task", "title": "Rotate credentials"}
    headers = {"Idempotency-Key": "create-abc"}

    first = api.post("/api/items", json=body, headers=headers)
    retry = api.post("/api/items", json=body, headers=headers)

    assert first.status_code == retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]
    assert retry.headers.get("Idempotent-Replayed") == "true"
    assert count(db, WorkItem) == 1


def test_double_click_sent_concurrently_creates_one_item(world, db):
    clients = [world.api(world.member) for _ in range(5)]
    body = {"team_id": world.support.id, "type": "task", "title": "Double clicked"}
    responses = run_concurrently(
        5, lambda i: clients[i].post("/api/items", json=body, headers={"Idempotency-Key": "same-click"})
    )
    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["id"] for r in responses}) == 1
    assert count(db, WorkItem) == 1


def test_reusing_a_key_for_a_different_request_is_rejected(world):
    api = world.api(world.member)
    api.post("/api/items", json={"team_id": world.support.id, "type": "task", "title": "First"},
             headers={"Idempotency-Key": "k1"})
    response = api.post("/api/items", json={"team_id": world.support.id, "type": "task", "title": "Second"},
                        headers={"Idempotency-Key": "k1"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"


def test_keys_are_scoped_per_user(world, db):
    body = {"team_id": world.support.id, "type": "task", "title": "Same key, different people"}
    world.api(world.member).post("/api/items", json=body, headers={"Idempotency-Key": "shared"})
    world.api(world.lead).post("/api/items", json=body, headers={"Idempotency-Key": "shared"})
    assert count(db, WorkItem) == 2


def test_retried_comment_posts_once(world, db):
    api = world.api(world.member)
    item = api.create_item(world.support.id)
    for _ in range(3):
        api.post(f"/api/items/{item['id']}/comments", json={"body": "On it"}, headers={"Idempotency-Key": "c1"})
    assert count(db, Comment) == 1


def test_failed_requests_are_not_remembered(world):
    """A request that failed (e.g. 409) can be retried with the same key once the problem is fixed."""
    api = world.api(world.lead)
    item = api.create_item(world.support.id)
    stale = api.patch(f"/api/items/{item['id']}", json={"priority": "P1"},
                      headers={"If-Match": "99", "Idempotency-Key": "edit-1"})
    assert stale.status_code == 409
    retry = api.patch(f"/api/items/{item['id']}", json={"priority": "P1"},
                      headers={"If-Match": "1", "Idempotency-Key": "edit-1"})
    assert retry.status_code == 200
    assert retry.headers.get("Idempotent-Replayed") is None
