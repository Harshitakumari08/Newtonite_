from __future__ import annotations
from sqlalchemy import text


def test_cursor_pagination_has_no_gaps_or_duplicates_even_with_identical_timestamps(world, db):
    api = world.api(world.lead)
    for i in range(53):
        api.create_item(world.support.id, title=f"Item number {i}")
    db.execute(text("UPDATE work_items SET updated_at = '2026-01-01T00:00:00Z'"))  # worst case: all ties
    db.commit()

    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 10} | ({"cursor": cursor} if cursor else {})
        page = api.get("/api/items", params=params).json()
        seen += [i["id"] for i in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 6
    assert len(seen) == len(set(seen)) == 53


def test_priority_sort_pages_in_order(world):
    api = world.api(world.lead)
    for p in ["P3", "P1", "P4", "P2", "P1", "P3"]:
        api.create_item(world.support.id, priority=p)
    first = api.get("/api/items", params={"sort": "priority", "limit": 4}).json()
    second = api.get("/api/items", params={"sort": "priority", "limit": 4, "cursor": first["next_cursor"]}).json()
    priorities = [i["priority"] for i in first["items"] + second["items"]]
    assert priorities == ["P1", "P1", "P2", "P3", "P3", "P4"]


def test_dashboard_views(world):
    lead, member = world.api(world.lead), world.api(world.member)
    urgent = lead.create_item(world.support.id, priority="P1", title="Urgent unassigned")
    lead.create_item(world.support.id, priority="P3", title="Not urgent")
    mine = lead.create_item(world.support.id, priority="P2", title="Will be mine")
    member.post(f"/api/items/{mine['id']}/claim")
    payment = member.create_item(world.payments.id, type="payment", title="Needs approval")
    claimed = member.post(f"/api/items/{payment['id']}/claim").json()
    member.transition(claimed, "submit")

    def titles(api, view):
        return {i["title"] for i in api.get("/api/items", params={"view": view}).json()["items"]}

    assert titles(lead, "unassigned_urgent") == {urgent["title"]}
    assert titles(member, "assigned_to_me") == {"Will be mine", "Needs approval"}
    assert titles(lead, "awaiting_my_approval") == {"Needs approval"}
    assert titles(member, "awaiting_my_approval") == set()  # members don't approve
    assert lead.get("/api/dashboard").json() == {
        "assigned_to_me": 0, "unassigned_urgent": 1, "awaiting_my_approval": 1
    }


def test_search_and_filters(world):
    api = world.api(world.lead)
    api.create_item(world.support.id, title="Refund not received", description="customer charged twice")
    api.create_item(world.support.id, title="Rotate API keys", type="task")
    api.create_item(world.payments.id, title="Refund dispute", type="payment")

    def titles(**params):
        return {i["title"] for i in api.get("/api/items", params=params).json()["items"]}

    assert titles(q="refund") == {"Refund not received", "Refund dispute"}
    assert titles(q="charged") == {"Refund not received"}
    assert titles(q="refund", team_id=world.payments.id) == {"Refund dispute"}
    assert titles(type="payment") == {"Refund dispute"}
    assert titles(assignee="none", status="OPEN") == {"Refund not received", "Rotate API keys", "Refund dispute"}


def test_bad_query_parameters_are_clear_errors(world):
    api = world.api(world.lead)
    assert api.get("/api/items", params={"cursor": "garbage"}).json()["error"]["code"] == "invalid_cursor"
    assert api.get("/api/items", params={"view": "everything"}).json()["error"]["code"] == "invalid_view"
    assert api.get("/api/items", params={"limit": 1000}).status_code == 422
