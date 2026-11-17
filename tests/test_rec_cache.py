"""The anonymous recommendation cache (application/api/rec_cache.py): an anonymous visitor's
answer for a product is computed once, then served from memory until the catalog or the
placement changes, an hour passes, or someone empties it."""
import pytest

import db
import rec_cache
from conftest import Q
from test_placements import PDP_SLUG, create_pdp_placement


@pytest.fixture(autouse=True)
def _free_plan(seeded):
    # On Pro, an anonymous answer follows the visitor's session and is not shared (see
    # test_pro_session_blend.py): the shared cache is what every Free workspace gets.
    db.set_client_plan(seeded.client_id, db.PLAN_FREE)

ANON = {"current_item_id": "SKU-NIKE-001", "session_id": "sess_a"}


def call(http, tenant, context, **body):
    response = http.post(f"/placements/{PDP_SLUG}/recommend", headers=tenant.public, json={"context": context, **body})
    assert response.status_code == 200, response.text
    return response


def test_second_anonymous_visitor_is_served_from_cache(http, seeded):
    create_pdp_placement(http, seeded, signals={"required": ["current_item_id"], "optional": ["session_id", "user_id"]})
    first = call(http, seeded, ANON)
    second = call(http, seeded, {**ANON, "session_id": "sess_b"})  # another visitor, same product
    assert first.headers["X-Likyly-Cache"] == "MISS"
    assert second.headers["X-Likyly-Cache"] == "HIT"
    assert [i["item_id"] for i in second.json()["items"]] == [i["item_id"] for i in first.json()["items"]]
    # Attribution is untouched: every call still gets its own recommendation id.
    assert second.json()["recommendation_id"] != first.json()["recommendation_id"]
    # Another product is its own entry.
    assert call(http, seeded, {**ANON, "current_item_id": "SKU-ADIDAS-007"}).headers["X-Likyly-Cache"] == "MISS"


def test_signed_in_visitor_is_never_cached(http, seeded):
    create_pdp_placement(http, seeded, signals={"required": ["current_item_id"], "optional": ["session_id", "user_id"]})
    for _ in range(2):
        assert call(http, seeded, {**ANON, "user_id": "user_42"}).headers["X-Likyly-Cache"] == "BYPASS"


def test_a_catalog_change_empties_it(http, seeded):
    create_pdp_placement(http, seeded)
    call(http, seeded, ANON)
    assert call(http, seeded, ANON).headers["X-Likyly-Cache"] == "HIT"
    http.put("/items/SKU-ADIDAS-007", params=Q, headers=seeded.secret, json={"title": "Adidas Adizero Boston 13", "description": "fast road running shoe"})
    assert call(http, seeded, ANON).headers["X-Likyly-Cache"] == "MISS"
    call(http, seeded, ANON)
    http.request("DELETE", "/items/SKU-ADIDAS-007", params=Q, headers=seeded.secret)
    refreshed = call(http, seeded, ANON)
    assert refreshed.headers["X-Likyly-Cache"] == "MISS"
    assert "SKU-ADIDAS-007" not in [i["item_id"] for i in refreshed.json()["items"]]


def test_a_placement_change_empties_it(http, seeded):
    create_pdp_placement(http, seeded)
    call(http, seeded, ANON)
    http.patch(f"/placements/{PDP_SLUG}", headers=seeded.secret, json={"limit": 2})
    changed = call(http, seeded, ANON)
    assert changed.headers["X-Likyly-Cache"] == "MISS"
    assert len(changed.json()["items"]) <= 2


def test_purge_by_api_key_and_isolation(http, seeded, other_tenant):
    create_pdp_placement(http, seeded)
    call(http, seeded, ANON)
    # Another account's purge never touches this one.
    assert http.post("/placements/cache/purge", headers=other_tenant.secret).json()["purged"] == 0
    assert call(http, seeded, ANON).headers["X-Likyly-Cache"] == "HIT"
    # The public key cannot purge.
    assert http.post(f"/placements/{PDP_SLUG}/cache/purge", headers=seeded.public).status_code == 403
    purged = http.post(f"/placements/{PDP_SLUG}/cache/purge", headers=seeded.secret)
    assert purged.status_code == 200 and purged.json()["purged"] == 1
    assert call(http, seeded, ANON).headers["X-Likyly-Cache"] == "MISS"
    assert http.post("/placements/nope/cache/purge", headers=seeded.secret).status_code == 404
    assert http.post("/placements/cache/purge", headers=seeded.secret).json()["purged"] == 1


def test_entries_expire():
    k = ("c", "slug")
    rec_cache.put(k, "value", ttl=10, now=100.0)
    assert rec_cache.get(k, now=105.0) == "value"
    assert rec_cache.get(k, now=111.0) is None


def test_a_home_block_follows_the_visit_even_after_a_first_visitor_filled_the_cache(http, seeded):
    """A home block (no current product) for an anonymous visitor: the first one, with no history,
    gets the popular items, and they are cached. A visitor who has since looked at products must
    still get what her session points to (session history), not that shared list - on the Magento
    sandbox's study of the home page, the block has to change once she has seen a product."""
    create_pdp_placement(http, seeded, slug="home", context_type="homepage", signals={"required": [], "optional": ["session_id", "user_id"]})
    first = call_slug(http, seeded, "home", {"session_id": "sess_new"})
    assert first.json()["strategy_used"] == "popular"
    assert call_slug(http, seeded, "home", {"session_id": "sess_other"}).headers["X-Likyly-Cache"] == "HIT"  # still shared
    http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": "sess_browsing", "item_id": "SKU-SALOMON-010"})
    after = call_slug(http, seeded, "home", {"session_id": "sess_browsing"})
    assert after.headers["X-Likyly-Cache"] != "HIT"
    assert after.json()["strategy_used"] == "session"


def call_slug(http, tenant, slug, context):
    response = http.post(f"/placements/{slug}/recommend", headers=tenant.public, json={"context": context})
    assert response.status_code == 200, response.text
    return response
