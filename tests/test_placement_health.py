"""get_placement_health / get_tracking_requirements / get_recent_integration_events - the
integration validator's backend (application/api/placement_service.py's get_health/
get_tracking_requirements/get_recent_events)."""
from conftest import Q

SLUG = "pdp-related"


def create_placement(http, tenant, **overrides):
    body = {
        "slug": SLUG, "name": "Related products", "context_type": "product_page",
        "product_type": "shop", "limit": 4, "strategy": "auto",
        "signals": {"required": ["current_item_id"], "optional": ["session_id"]},
        **overrides,
    }
    response = http.post("/placements", headers=tenant.secret, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_tracking_requirements_lists_the_five_events(http, seeded):
    create_placement(http, seeded)
    requirements = http.get(f"/placements/{SLUG}/tracking-requirements", headers=seeded.secret).json()
    event_types = {e["event_type"] for e in requirements["required_events"]}
    assert event_types == {"recommendation_impression", "recommendation_click", "product_view", "add_to_cart", "purchase"}
    assert requirements["required_context"]["required"] == ["current_item_id"]


def test_default_tracked_events_depend_on_context_type(http, seeded):
    # A cart placement's visitor is already past "viewing" - product_view isn't a useful
    # default there. A content-page placement may never touch cart/purchase at all.
    create_placement(http, seeded, slug="cart-cross-sell", context_type="cart", signals={"required": [], "optional": ["session_id"]})
    cart_requirements = http.get("/placements/cart-cross-sell/tracking-requirements", headers=seeded.secret).json()
    cart_events = {e["event_type"] for e in cart_requirements["required_events"] if e["scope"] == "catalog"}
    assert cart_events == {"add_to_cart", "purchase"}

    create_placement(http, seeded, slug="article-related", context_type="content_page", signals={"required": [], "optional": ["session_id"]})
    content_requirements = http.get("/placements/article-related/tracking-requirements", headers=seeded.secret).json()
    content_events = {e["event_type"] for e in content_requirements["required_events"] if e["scope"] == "catalog"}
    assert content_events == {"product_view"}

    cart_health = http.get("/placements/cart-cross-sell/health", headers=seeded.secret).json()
    cart_check_names = {c["name"] for c in cart_health["checks"]}
    assert "add_to_cart_received" in cart_check_names
    assert "product_views_received" not in cart_check_names


def test_tracking_requirements_and_health_use_configured_event_types_when_set(http, seeded):
    # tracking_configuration.event_types (set via PATCH, e.g. from the dashboard's placement
    # edit form) replaces the default product_view/add_to_cart/purchase trio - a tenant's own
    # custom event type ("reservation") becomes what this placement is tracked against.
    create_placement(http, seeded)
    updated = http.patch(f"/placements/{SLUG}", headers=seeded.secret, json={"tracking_configuration": {"event_types": ["reservation"]}})
    assert updated.status_code == 200, updated.text

    requirements = http.get(f"/placements/{SLUG}/tracking-requirements", headers=seeded.secret).json()
    catalog_events = {e["event_type"] for e in requirements["required_events"] if e["scope"] == "catalog"}
    assert catalog_events == {"reservation"}
    placement_events = {e["event_type"] for e in requirements["required_events"] if e["scope"] == "placement"}
    assert placement_events == {"recommendation_impression", "recommendation_click"}  # always present, unaffected

    health = http.get(f"/placements/{SLUG}/health", headers=seeded.secret).json()
    statuses = {c["name"]: c["status"] for c in health["checks"]}
    assert "reservation_received" in statuses
    assert statuses["reservation_received"] == "warning"  # unknown custom type: not the hard "missing" purchase gets
    assert "purchases_received" not in statuses
    assert "add_to_cart_received" not in statuses
    assert "product_views_received" not in statuses


def test_health_before_any_activity_is_all_missing_or_warning(http, seeded):
    create_placement(http, seeded)
    health = http.get(f"/placements/{SLUG}/health", headers=seeded.secret).json()
    assert health["overall"] == "missing"
    statuses = {c["name"]: c["status"] for c in health["checks"]}
    assert statuses["recommendations_requested"] == "missing"
    assert statuses["purchases_received"] == "missing"
    assert statuses["add_to_cart_received"] == "warning"


def test_health_reflects_real_activity(http, seeded):
    create_placement(http, seeded)

    recommend = http.post(f"/placements/{SLUG}/recommend", headers=seeded.public, json={
        "context": {"current_item_id": "SKU-NIKE-001", "session_id": "sess_1"},
    })
    assert recommend.status_code == 200, recommend.text
    recommendation_id = recommend.json()["recommendation_id"]

    http.post("/events/recommendation_impression", params=Q, headers=seeded.public, json={
        "session_id": "sess_1", "item_id": "SKU-ADIDAS-007", "recommendation_id": recommendation_id, "placement": SLUG,
    })
    http.post("/events/recommendation_click", params=Q, headers=seeded.public, json={
        "session_id": "sess_1", "item_id": "SKU-ADIDAS-007", "recommendation_id": recommendation_id, "placement": SLUG,
    })
    http.post("/events/product_view", params=Q, headers=seeded.public, json={"session_id": "sess_1", "item_id": "SKU-ADIDAS-007"})
    http.post("/events/purchase", params=Q, headers=seeded.public, json={"session_id": "sess_1", "item_id": "SKU-ADIDAS-007", "event_id": "evt_1"})

    health = http.get(f"/placements/{SLUG}/health", headers=seeded.secret).json()
    statuses = {c["name"]: c["status"] for c in health["checks"]}
    assert statuses["recommendations_requested"] == "ok"
    assert statuses["current_item_supplied"] == "ok"
    assert statuses["anonymous_session_supplied"] == "ok"
    assert statuses["impressions_received"] == "ok"
    assert statuses["recommendation_clicks_received"] == "ok"
    assert statuses["product_views_received"] == "ok"
    assert statuses["purchases_received"] == "ok"
    assert statuses["add_to_cart_received"] == "warning"  # still never received
    assert health["overall"] == "warning"  # worst remaining check


def test_health_omits_irrelevant_signal_checks(http, seeded):
    """A homepage placement that declares no context signals shouldn't be told it's missing
    "current item supplied" - that check only applies when the placement actually uses it."""
    create_placement(http, seeded, slug="homepage", context_type="homepage", signals={"required": [], "optional": []})
    health = http.get("/placements/homepage/health", headers=seeded.secret).json()
    names = {c["name"] for c in health["checks"]}
    assert "current_item_supplied" not in names
    assert "anonymous_session_supplied" not in names


def test_recent_events_returns_the_raw_rows(http, seeded):
    create_placement(http, seeded)
    recommend = http.post(f"/placements/{SLUG}/recommend", headers=seeded.public, json={"context": {"current_item_id": "SKU-NIKE-001"}})
    recommendation_id = recommend.json()["recommendation_id"]
    http.post("/events/recommendation_impression", params=Q, headers=seeded.public, json={
        "session_id": "s1", "item_id": "SKU-ADIDAS-007", "recommendation_id": recommendation_id, "placement": SLUG,
    })

    recent = http.get(f"/placements/{SLUG}/recent-events", headers=seeded.secret).json()
    assert len(recent["recommendation_calls"]) == 1
    assert recent["recommendation_calls"][0]["item_id"] == "SKU-NIKE-001"
    assert len(recent["interactions"]) == 1
    assert recent["interactions"][0]["event_type"] == "impression"
    assert recent["interactions"][0]["item_id"] == "SKU-ADIDAS-007"


def test_health_and_tracking_requirements_are_tenant_isolated(http, seeded, other_tenant):
    create_placement(http, seeded)
    assert http.get(f"/placements/{SLUG}/health", headers=other_tenant.secret).status_code == 404
    assert http.get(f"/placements/{SLUG}/tracking-requirements", headers=other_tenant.secret).status_code == 404
    assert http.get(f"/placements/{SLUG}/recent-events", headers=other_tenant.secret).status_code == 404


def test_health_requires_placements_read_scope_not_public_key(http, seeded):
    create_placement(http, seeded)
    assert http.get(f"/placements/{SLUG}/health", headers=seeded.public).status_code == 403
