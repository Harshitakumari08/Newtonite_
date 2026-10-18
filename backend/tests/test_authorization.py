from __future__ import annotations
import pytest


def test_non_members_get_404_not_403(world):
    item = world.api(world.member).create_item(world.support.id)
    outsider = world.api(world.outsider)  # only in Payments
    for method, path, kwargs in [
        ("get", f"/api/items/{item['id']}", {}),
        ("get", f"/api/items/{item['id']}/activity", {}),
        ("post", f"/api/items/{item['id']}/claim", {}),
        ("post", f"/api/items/{item['id']}/comments", {"json": {"body": "hi"}}),
        ("patch", f"/api/items/{item['id']}", {"json": {"title": "pwned"}, "headers": {"If-Match": "1"}}),
    ]:
        response = getattr(outsider, method)(path, **kwargs)
        assert response.status_code == 404, (method, path)


def test_lists_only_show_items_from_my_teams(world):
    world.api(world.lead).create_item(world.support.id, title="Support only")
    world.api(world.lead).create_item(world.payments.id, title="Payments only")
    titles = {i["title"] for i in world.api(world.outsider).get("/api/items").json()["items"]}
    assert titles == {"Payments only"}
    # Asking for another team's items explicitly returns nothing rather than leaking them.
    assert world.api(world.outsider).get(f"/api/items?team_id={world.support.id}").json()["items"] == []


def test_viewer_can_read_and_comment_but_not_change(world):
    item = world.api(world.member).create_item(world.support.id)
    viewer = world.api(world.viewer)
    assert viewer.get(f"/api/items/{item['id']}").status_code == 200
    assert viewer.post(f"/api/items/{item['id']}/comments", json={"body": "FYI"}).status_code == 201
    assert viewer.post(f"/api/items/{item['id']}/claim").status_code == 403
    assert viewer.patch(f"/api/items/{item['id']}", json={"priority": "P1"}, headers={"If-Match": "1"}).status_code == 403
    assert viewer.post("/api/items", json={"team_id": world.support.id, "type": "task", "title": "nope"}).status_code == 403


def test_member_cannot_change_someone_elses_item(world):
    lead = world.api(world.lead)
    item = lead.create_item(world.support.id)
    claimed = world.api(world.member2).post(f"/api/items/{item['id']}/claim").json()
    member = world.api(world.member)  # same team, not the assignee or creator
    v = str(claimed["version"])
    assert member.patch(f"/api/items/{item['id']}", json={"priority": "P1"}, headers={"If-Match": v}).status_code == 403
    assert member.transition(claimed, "resolve").status_code == 403
    assert member.post(f"/api/items/{item['id']}/assign", json={"assignee_id": world.member.id},
                       headers={"If-Match": v}).status_code == 403


def test_only_leads_can_assign_and_only_to_team_members(world):
    lead = world.api(world.lead)
    item = lead.create_item(world.support.id)
    to_viewer = lead.post(f"/api/items/{item['id']}/assign", json={"assignee_id": world.viewer.id},
                          headers={"If-Match": "1"})
    assert to_viewer.status_code == 422  # viewers can't own work
    to_outsider = lead.post(f"/api/items/{item['id']}/assign", json={"assignee_id": world.outsider.id},
                            headers={"If-Match": "1"})
    assert to_outsider.status_code == 422  # not in Support
    ok = lead.post(f"/api/items/{item['id']}/assign", json={"assignee_id": world.member.id}, headers={"If-Match": "1"})
    assert ok.status_code == 200 and ok.json()["assignee"]["id"] == world.member.id


def test_allowed_actions_reflect_the_callers_permissions(world):
    item = world.api(world.lead).create_item(world.support.id)
    viewer_actions = world.api(world.viewer).get(f"/api/items/{item['id']}").json()["allowed_actions"]
    member_actions = world.api(world.member).get(f"/api/items/{item['id']}").json()["allowed_actions"]
    lead_actions = world.api(world.lead).get(f"/api/items/{item['id']}").json()["allowed_actions"]
    assert viewer_actions == ["comment"]
    assert "claim" in member_actions and "assign" not in member_actions
    assert {"claim", "assign", "cancel", "edit"} <= set(lead_actions)


@pytest.mark.parametrize("header", [None, "Bearer not-a-jwt", "Basic abc"])
def test_requests_without_a_valid_token_are_rejected(world, header):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    headers = {"Authorization": header} if header else {}
    assert client.get("/api/items", headers=headers).status_code == 401


def test_admin_has_lead_access_across_all_teams(world):
    # Admin has no explicit rows in memberships table, but is_admin=True
    admin = world.api(world.admin)

    # 1. GET /api/me returns all teams as LEAD
    me_resp = admin.get("/api/me")
    assert me_resp.status_code == 200
    me = me_resp.json()
    assert me["is_admin"] is True
    assert len(me["memberships"]) == 2
    team_roles = {m["team"]["name"]: m["role"] for m in me["memberships"]}
    assert team_roles == {"Support": "LEAD", "Payments": "LEAD"}

    # 2. Admin can create items in any team
    support_item = admin.create_item(world.support.id, title="Admin Support Item")
    assert support_item["team"]["id"] == world.support.id

    payments_item = admin.create_item(world.payments.id, title="Admin Payments Item")
    assert payments_item["team"]["id"] == world.payments.id

    # 3. Admin can filter items by specific team
    support_list = admin.get(f"/api/items?team_id={world.support.id}").json()["items"]
    assert any(i["id"] == support_item["id"] for i in support_list)
    assert not any(i["id"] == payments_item["id"] for i in support_list)
