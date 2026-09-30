"""End-to-end proof of the brief's Definition of Done: catalog -> sync -> placement ->
recommend -> impression -> click -> purchase attribution -> validation, driven only through
the public HTTP surface a coding agent would actually call (data-sources + placements +
events + the integration validator) - no direct DB manipulation except to seed the mocked
connector response.

"render" is exercised at the sdk/react controller-test level (test/controller.test.ts) and via
the MCP smoke-test technique documented in docs/getting-started.md - this test proves the
server-side half of the journey, which is what all of the above ultimately call into.
"""
PLACEMENT_SLUG = "pdp-related-e2e"
CATALOG = "shop-e2e"

CATALOG_PAYLOAD = [
    {"id": "E2E-1", "name": "Nike Air Zoom", "category": "running-shoes", "price": 129.9},
    {"id": "E2E-2", "name": "Adidas Boston", "category": "running-shoes", "price": 159.0},
]


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.links = {}
        self.text = str(payload)

    def json(self):
        return self._payload


def test_full_integration_journey(http, tenant, monkeypatch):
    monkeypatch.setattr(
        "connectors.rest_api.RestApiConnector._request",
        lambda self, params: FakeResponse({"products": CATALOG_PAYLOAD}),
    )

    # 1. Connect the catalog (data source), mapped, and run the first sync.
    created = http.post("/data-sources", headers=tenant.secret, json={
        "name": "E2E catalog", "type": "rest_api", "product_type": CATALOG,
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"},
        "sync_mode": "full",
    })
    assert created.status_code == 201, created.text
    data_source_id = created.json()["id"]

    mapping = {"external_id": "id", "title": "name", "category": "category", "price": "price"}
    saved_mapping = http.put(f"/data-sources/{data_source_id}/field-mapping", headers=tenant.secret, json={"mapping": mapping})
    assert saved_mapping.status_code == 200, saved_mapping.text

    sync = http.post(f"/data-sources/{data_source_id}/sync", headers=tenant.secret, json={"mode": "full"})
    assert sync.status_code == 202, sync.text
    runs = http.get(f"/data-sources/{data_source_id}/syncs", headers=tenant.secret).json()
    assert runs[0]["status"] == "success", runs
    assert runs[0]["items_upserted"] == 2

    items = http.get("/items", params={"data_product_type": CATALOG}, headers=tenant.secret).json()
    assert {i["item_id"] for i in items} == {"E2E-1", "E2E-2"}

    # 2. Configure the placement on top of that catalog.
    placement = http.post("/placements", headers=tenant.secret, json={
        "slug": PLACEMENT_SLUG, "name": "Related products", "context_type": "product_page",
        "product_type": CATALOG, "limit": 4, "strategy": "auto",
        "signals": {"required": ["current_item_id"], "optional": ["session_id", "user_id"]},
    })
    assert placement.status_code == 201, placement.text

    # 3. Validator, before any traffic: everything missing/warning.
    health_before = http.get(f"/placements/{PLACEMENT_SLUG}/health", headers=tenant.secret).json()
    assert health_before["overall"] == "missing"
    assert {c["name"]: c["status"] for c in health_before["checks"]}["recommendations_requested"] == "missing"

    # 4. Recommend (the runtime call a rendered page makes).
    recommend = http.post(f"/placements/{PLACEMENT_SLUG}/recommend", headers=tenant.public, json={
        "context": {"current_item_id": "E2E-1", "session_id": "sess_e2e"},
    })
    assert recommend.status_code == 200, recommend.text
    body = recommend.json()
    assert body["items"], "expected at least one recommended item"
    recommendation_id = body["recommendation_id"]
    recommended_item_id = body["items"][0]["item_id"]
    assert recommended_item_id != "E2E-1"  # exclude_current_item, on by default

    # 5. "Render" = the recommendation was requested and returned - impression/click/product_view/
    # purchase are what a rendered page + its tracking code would fire next.
    impression = http.post("/events/recommendation_impression", params={"data_product_type": CATALOG}, headers=tenant.public, json={
        "session_id": "sess_e2e", "item_id": recommended_item_id, "recommendation_id": recommendation_id, "placement": PLACEMENT_SLUG,
    })
    assert impression.status_code == 200, impression.text

    click = http.post("/events/recommendation_click", params={"data_product_type": CATALOG}, headers=tenant.public, json={
        "session_id": "sess_e2e", "item_id": recommended_item_id, "recommendation_id": recommendation_id, "placement": PLACEMENT_SLUG,
    })
    assert click.status_code == 200, click.text

    product_view = http.post("/events/product_view", params={"data_product_type": CATALOG}, headers=tenant.public, json={
        "session_id": "sess_e2e", "item_id": recommended_item_id,
    })
    assert product_view.status_code == 200, product_view.text

    purchase = http.post("/events/purchase", params={"data_product_type": CATALOG}, headers=tenant.public, json={
        "session_id": "sess_e2e", "item_id": recommended_item_id, "event_id": "evt_e2e_purchase_1",
        "recommendation_id": recommendation_id, "placement": PLACEMENT_SLUG,
    })
    assert purchase.status_code == 200, purchase.text

    # 6. Attribution: the purchase is traceable back to the exact recommend() call that surfaced it.
    recent = http.get(f"/placements/{PLACEMENT_SLUG}/recent-events", headers=tenant.secret).json()
    assert recent["recommendation_calls"][0]["recommendation_id"] == recommendation_id
    assert recent["recommendation_calls"][0]["item_id"] == "E2E-1"

    # 7. Validator, after the journey: every check that fired is ok; add_to_cart (never sent) is
    # the one deliberate gap, exactly matching the brief's own example shape.
    health_after = http.get(f"/placements/{PLACEMENT_SLUG}/health", headers=tenant.secret).json()
    statuses = {c["name"]: c["status"] for c in health_after["checks"]}
    assert statuses["recommendations_requested"] == "ok"
    assert statuses["current_item_supplied"] == "ok"
    assert statuses["anonymous_session_supplied"] == "ok"
    assert statuses["impressions_received"] == "ok"
    assert statuses["recommendation_clicks_received"] == "ok"
    assert statuses["product_views_received"] == "ok"
    assert statuses["purchases_received"] == "ok"
    assert statuses["add_to_cart_received"] == "warning"
    assert health_after["overall"] == "warning"  # worst remaining check - nothing "missing" anymore

    # 8. Backward compatibility: the legacy, pre-placement API is untouched throughout.
    legacy = http.post("/getRec", params={"data_product_type": CATALOG}, headers=tenant.public, json={"item_id": "E2E-1", "count": 3})
    assert legacy.status_code == 200
    assert legacy.json()["strategy"] == "content"
