from __future__ import annotations
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.models import ActivityEvent, EventKind, Membership, Role, User


def run_concurrently(n: int, fn):
    """Start n calls at (nearly) the same instant and return their results."""
    barrier = threading.Barrier(n)

    def call(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(call, range(n)))


def test_simultaneous_claims_exactly_one_wins(world, db):
    # Ten members of the team all click "claim" at the same moment.
    claimants = []
    for i in range(10):
        user = User(name=f"Racer {i}", email=f"racer{i}@test.dev", password_hash="x")
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, team_id=world.support.id, role=Role.MEMBER))
        claimants.append(user)
    db.commit()
    item = world.api(world.lead).create_item(world.support.id)
    clients = [world.api(u) for u in claimants]

    responses = run_concurrently(10, lambda i: clients[i].post(f"/api/items/{item['id']}/claim"))

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] + [409] * 9
    winner = next(r.json() for r in responses if r.status_code == 200)
    losers = [r.json() for r in responses if r.status_code == 409]
    assert all(body["error"]["code"] == "already_claimed" for body in losers)
    # Losers are told who actually won, so the UI can show it.
    assert all(body["error"]["details"]["current"]["assignee"]["id"] == winner["assignee"]["id"] for body in losers)

    detail = world.api(world.lead).get(f"/api/items/{item['id']}").json()
    assert detail["status"] == "IN_PROGRESS"
    assert detail["version"] == item["version"] + 1  # bumped exactly once
    assigned_events = db.execute(
        select(func.count()).where(ActivityEvent.item_id == item["id"], ActivityEvent.kind == EventKind.ASSIGNED)
    ).scalar_one()
    assert assigned_events == 1


def test_claiming_twice_by_the_same_user_is_harmless(world):
    api = world.api(world.member)
    item = api.create_item(world.support.id)
    first = api.post(f"/api/items/{item['id']}/claim")
    second = api.post(f"/api/items/{item['id']}/claim")
    assert first.status_code == second.status_code == 200
    assert second.json()["version"] == first.json()["version"]


def test_stale_edit_is_rejected_with_the_current_item(world):
    alice, bob = world.api(world.member), world.api(world.lead)
    item = alice.create_item(world.support.id)

    # Both load version 1. Bob saves first.
    ok = bob.patch(f"/api/items/{item['id']}", json={"priority": "P1"}, headers={"If-Match": "1"})
    assert ok.status_code == 200 and ok.json()["version"] == 2

    stale = alice.patch(f"/api/items/{item['id']}", json={"title": "Alice's older idea"}, headers={"If-Match": "1"})
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "version_conflict"
    assert error["details"]["current"]["priority"] == "P1"  # Alice sees what Bob changed
    assert error["details"]["current"]["title"] == item["title"]  # and nothing was overwritten


def test_simultaneous_edits_on_the_same_version_only_one_applies(world):
    api_a, api_b = world.api(world.lead), world.api(world.member)
    item = api_a.create_item(world.support.id)
    world.api(world.member).post(f"/api/items/{item['id']}/claim")  # version 2, member is assignee
    apis = [api_a, api_b]
    responses = run_concurrently(
        2,
        lambda i: apis[i].patch(f"/api/items/{item['id']}", json={"priority": ["P1", "P4"][i]}, headers={"If-Match": "2"}),
    )
    assert sorted(r.status_code for r in responses) == [200, 409]


def test_writes_without_if_match_are_refused(world):
    api = world.api(world.lead)
    item = api.create_item(world.support.id)
    response = api.patch(f"/api/items/{item['id']}", json={"priority": "P1"})
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "precondition_required"


def test_comments_do_not_make_other_peoples_edits_stale(world):
    lead, member = world.api(world.lead), world.api(world.member)
    item = lead.create_item(world.support.id)
    assert member.post(f"/api/items/{item['id']}/comments", json={"body": "Looking at logs"}).status_code == 201
    response = lead.patch(f"/api/items/{item['id']}", json={"priority": "P1"}, headers={"If-Match": str(item["version"])})
    assert response.status_code == 200


def test_assign_requires_if_match_and_rejects_stale_version(world):
    lead, member = world.api(world.lead), world.api(world.member)
    item = lead.create_item(world.support.id)

    # 1. Missing If-Match returns 428 Precondition Required
    res_missing_header = lead.post(f"/api/items/{item['id']}/assign", json={"assignee_id": world.member.id})
    assert res_missing_header.status_code == 428
    assert res_missing_header.json()["error"]["code"] == "precondition_required"

    # 2. Stale If-Match returns 409 Conflict
    res_stale = lead.post(
        f"/api/items/{item['id']}/assign",
        json={"assignee_id": world.member.id},
        headers={"If-Match": "0"},
    )
    assert res_stale.status_code == 409
    assert res_stale.json()["error"]["code"] == "version_conflict"

    # 3. Correct If-Match returns 200 OK and assigns item
    res_ok = lead.post(
        f"/api/items/{item['id']}/assign",
        json={"assignee_id": world.member.id},
        headers={"If-Match": str(item["version"])},
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["assignee"]["id"] == world.member.id
    assert res_ok.json()["version"] == item["version"] + 1
