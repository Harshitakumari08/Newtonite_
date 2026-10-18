from __future__ import annotations
def titles(response):
    assert response.status_code == 200, response.text
    return [i["title"] for i in response.json()]


def test_similar_items_share_words_and_rank_closest_first(world):
    api = world.api(world.lead)
    item = api.create_item(world.support.id, title="Refund not received", description="Customer was charged twice")
    api.create_item(world.support.id, title="Customer charged twice for refund", description="Duplicate charge")
    api.create_item(world.support.id, title="Refund delayed")
    api.create_item(world.support.id, title="Rotate API keys")

    # Matches on stems too: "charged" finds "charge". The item itself is never in its own list.
    assert titles(api.get(f"/api/items/{item['id']}/similar")) == ["Customer charged twice for refund", "Refund delayed"]
    assert titles(api.get(f"/api/items/{item['id']}/similar", params={"limit": 1})) == [
        "Customer charged twice for refund"
    ]


def test_similar_items_respect_team_permissions(world):
    lead = world.api(world.lead)
    item = lead.create_item(world.support.id, title="Refund not received")
    lead.create_item(world.payments.id, title="Refund dispute", type="payment")

    # The viewer is only in Support, so the Payments item never appears for them...
    assert titles(world.api(world.viewer).get(f"/api/items/{item['id']}/similar")) == []
    # ...while the lead, who is in both teams, sees it.
    assert titles(lead.get(f"/api/items/{item['id']}/similar")) == ["Refund dispute"]
    # And someone outside Support can't use the item as a starting point at all.
    assert world.api(world.outsider).get(f"/api/items/{item['id']}/similar").status_code == 404


def test_item_with_only_stop_words_has_no_similar_items(world):
    api = world.api(world.lead)
    item = api.create_item(world.support.id, title="It is what it is")
    api.create_item(world.support.id, title="What is this")
    assert titles(api.get(f"/api/items/{item['id']}/similar")) == []
