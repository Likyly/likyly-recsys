"""Pro feature: an anonymous visitor on a product page who already browsed this session gets
the current product's neighbours mixed with what their session points to. Free workspaces get
the current product's neighbours only (and share the anonymous cache)."""
import db
from conftest import Q
from test_placements import PDP_SLUG, create_pdp_placement

TIMBERLAND = "gid://shopify/Product/123456"
DR_MARTENS = "3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f"


def browse_boots(http, tenant, session):
    # The visitor looked at one boot: the session side suggests what is close to it.
    for item in [TIMBERLAND]:
        assert http.post("/events/view", params=Q, headers=tenant.public, json={"session_id": session, "item_id": item}).status_code == 200


def recommend(http, tenant, context):
    response = http.post(f"/placements/{PDP_SLUG}/recommend", headers=tenant.public, json={"context": context})
    assert response.status_code == 200, response.text
    return response


def test_pro_mixes_the_product_with_the_session(http, seeded):
    db.set_client_plan(seeded.client_id, db.PLAN_PRO)
    create_pdp_placement(http, seeded)
    browse_boots(http, seeded, "sess_boots")
    response = recommend(http, seeded, {"current_item_id": "SKU-NIKE-001", "session_id": "sess_boots"})
    body = response.json()
    items = [i["item_id"] for i in body["items"]]
    assert body["strategy_used"] == "content_session"
    assert items and items[0] != "SKU-NIKE-001" and "SKU-NIKE-001" not in items
    assert DR_MARTENS in items, f"the boot close to what the visitor browsed should show up next to running shoes: {items}"
    assert TIMBERLAND not in items, "what the visitor already saw is not suggested again"
    assert len(items) == len(set(items))
    assert response.headers["X-Likyly-Cache"] == "BYPASS", "a session-shaped answer is never shared"


def test_pro_without_session_history_stays_on_the_product(http, seeded):
    db.set_client_plan(seeded.client_id, db.PLAN_PRO)
    create_pdp_placement(http, seeded)
    body = recommend(http, seeded, {"current_item_id": "SKU-NIKE-001", "session_id": "sess_new"}).json()
    assert body["strategy_used"] == "content"


def test_free_keeps_to_the_product(http, seeded):
    db.set_client_plan(seeded.client_id, db.PLAN_FREE)
    create_pdp_placement(http, seeded)
    browse_boots(http, seeded, "sess_boots")
    response = recommend(http, seeded, {"current_item_id": "SKU-NIKE-001", "session_id": "sess_boots"})
    assert response.json()["strategy_used"] == "content"
    assert response.headers["X-Likyly-Cache"] == "MISS"
