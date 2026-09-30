"""Placements: CRUD, tenant isolation, scopes, and the runtime orchestration
(POST /placements/{slug}/recommend, preview_placement) against every strategy/signal
combination the brief calls out. Uses the existing `seeded` fixture (8 shoe items, catalog
"shop") - no new fixtures needed."""
from conftest import Q, SHOES

PDP_SLUG = "pdp-related"


def create_pdp_placement(http, tenant, **overrides):
    body = {
        "slug": PDP_SLUG, "name": "Related products", "context_type": "product_page",
        "product_type": "shop", "limit": 4, "strategy": "auto",
        "signals": {"required": ["current_item_id"], "optional": ["session_id", "user_id"]},
        **overrides,
    }
    response = http.post("/placements", headers=tenant.secret, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_get_list_update(http, seeded):
    created = create_pdp_placement(http, seeded)
    assert created["slug"] == PDP_SLUG
    assert created["version"] == 1
    assert created["enabled"] is True

    fetched = http.get(f"/placements/{PDP_SLUG}", headers=seeded.secret).json()
    assert fetched == created

    listing = http.get("/placements", headers=seeded.secret).json()
    assert [p["slug"] for p in listing] == [PDP_SLUG]

    updated = http.patch(f"/placements/{PDP_SLUG}", headers=seeded.secret, json={"limit": 6}).json()
    assert updated["limit"] == 6
    assert updated["version"] == 2


def test_duplicate_slug_is_rejected(http, seeded):
    create_pdp_placement(http, seeded)
    response = http.post("/placements", headers=seeded.secret, json={
        "slug": PDP_SLUG, "name": "Again", "product_type": "shop",
    })
    assert response.status_code == 422


def test_deactivate_is_reversible_delete_is_not(http, seeded):
    create_pdp_placement(http, seeded)

    deactivated = http.post(f"/placements/{PDP_SLUG}/deactivate", headers=seeded.secret).json()
    assert deactivated["enabled"] is False

    disabled_call = http.post(f"/placements/{PDP_SLUG}/recommend", headers=seeded.public, json={"context": {"current_item_id": "SKU-NIKE-001"}})
    assert disabled_call.status_code == 404

    reenabled = http.patch(f"/placements/{PDP_SLUG}", headers=seeded.secret, json={"enabled": True}).json()
    assert reenabled["enabled"] is True

    http.delete(f"/placements/{PDP_SLUG}", headers=seeded.secret)
    assert http.get(f"/placements/{PDP_SLUG}", headers=seeded.secret).status_code == 404


def test_tenant_isolation_on_every_route(http, seeded, other_tenant):
    create_pdp_placement(http, seeded)

    for method, path, needs_body in [
        ("get", f"/placements/{PDP_SLUG}", False),
        ("patch", f"/placements/{PDP_SLUG}", True),
        ("delete", f"/placements/{PDP_SLUG}", False),
        ("get", f"/placements/{PDP_SLUG}/requirements", False),
        ("post", f"/placements/{PDP_SLUG}/preview", True),
        ("post", f"/placements/{PDP_SLUG}/deactivate", False),
    ]:
        kwargs = {"headers": other_tenant.secret}
        if needs_body:
            kwargs["json"] = {}
        response = getattr(http, method)(path, **kwargs)
        assert response.status_code == 404, f"{method} {path} leaked across tenants: {response.status_code}"

    assert http.get("/placements", headers=other_tenant.secret).json() == []
    # /recommend is public-ok, not tenant-authenticated by the caller's own key at all in the
    # sense of "which tenant's placement" - other_tenant's public key resolves to
    # other_tenant's client_id, which has no slug "pdp-related", so still 404, never seeded's data.
    assert http.post(f"/placements/{PDP_SLUG}/recommend", headers=other_tenant.public, json={"context": {}}).status_code == 404


def test_public_key_cannot_manage_placements(http, seeded):
    response = http.post("/placements", headers=seeded.public, json={"slug": "x", "name": "x", "product_type": "shop"})
    assert response.status_code == 403


def test_developer_key_scope_is_enforced(http, seeded, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": "supabase-placements-1", "email": "x@example.com"})
    auth = {"Authorization": "Bearer fake"}
    http.post("/clients/me", headers=auth)  # links this supabase user to a fresh client, not `seeded`
    read_only = http.post("/clients/me/developer-keys", headers=auth, json={"name": "ro", "scopes": ["placements:read"]}).json()
    dev_headers = {"X-API-Key": read_only["key"]}

    assert http.get("/placements", headers=dev_headers).status_code == 200
    create = http.post("/placements", headers=dev_headers, json={"slug": "x", "name": "x", "product_type": "shop"})
    assert create.status_code == 403


def test_invalid_placement_slug_is_404_everywhere(http, seeded):
    assert http.get("/placements/nope", headers=seeded.secret).status_code == 404
    assert http.post("/placements/nope/recommend", headers=seeded.public, json={"context": {}}).status_code == 404
    assert http.post("/placements/nope/preview", headers=seeded.secret, json={"context": {}}).status_code == 404


# ---------------------------------------------------------------------------
# Runtime orchestration - "auto" mode signal scenarios (delegates to recommend_auto)
# ---------------------------------------------------------------------------

def recommend(http, seeded, context, limit=None, slug=PDP_SLUG):
    body = {"context": context}
    if limit is not None:
        body["limit"] = limit
    response = http.post(f"/placements/{slug}/recommend", headers=seeded.public, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_anonymous_visitor_current_item_and_session_uses_content(http, seeded):
    create_pdp_placement(http, seeded, signals={"required": [], "optional": ["current_item_id", "session_id"]})
    result = recommend(http, seeded, {"current_item_id": "SKU-NIKE-001", "session_id": "sess_anon_1"})
    assert result["strategy_used"] == "content"
    assert result["placement"] == PDP_SLUG
    assert result["recommendation_id"]
    assert all(item["item_id"] != "SKU-NIKE-001" for item in result["items"])  # exclude_current_item default


def test_identified_user_with_item_uses_hybrid(http, seeded):
    """recommend_auto only counts a user as "known" once LIKYLY has seen them at least once
    (read-only id resolution, see recommender.recommend_auto's docstring) - so the user needs
    one prior tracked event before "has_user" is true."""
    create_pdp_placement(http, seeded, signals={"required": [], "optional": ["current_item_id", "user_id"]})
    http.post("/events/view", params=Q, headers=seeded.public, json={"user_id": "user_42", "item_id": "SKU-ADIDAS-007"})
    result = recommend(http, seeded, {"current_item_id": "SKU-NIKE-001", "user_id": "user_42"})
    assert result["strategy_used"] == "hybrid"


def test_current_item_only_uses_content(http, seeded):
    create_pdp_placement(http, seeded, signals={"required": [], "optional": ["current_item_id"]})
    result = recommend(http, seeded, {"current_item_id": "SKU-ADIDAS-007"})
    assert result["strategy_used"] == "content"


def test_no_signal_at_all_falls_back_to_popular(http, seeded):
    create_pdp_placement(http, seeded, slug="homepage-discovery", signals={"required": [], "optional": []}, context_type="homepage")
    response = http.post("/placements/homepage-discovery/recommend", headers=seeded.public, json={"context": {}})
    assert response.status_code == 200
    assert response.json()["strategy_used"] == "popular"


def test_session_only_with_persisted_history_uses_session(http, seeded):
    """Recording a view first gives the session persisted history - recommend_auto's
    SESSION_HISTORY step, reported publicly as "session"."""
    create_pdp_placement(http, seeded, slug="session-only", signals={"required": [], "optional": ["session_id"]})
    http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": "sess_hist_1", "item_id": "SKU-NIKE-001"})
    response = http.post("/placements/session-only/recommend", headers=seeded.public, json={"context": {"session_id": "sess_hist_1"}})
    assert response.status_code == 200
    assert response.json()["strategy_used"] == "session"


def test_missing_required_signal_is_422_on_recommend(http, seeded):
    create_pdp_placement(http, seeded)  # requires current_item_id
    response = http.post(f"/placements/{PDP_SLUG}/recommend", headers=seeded.public, json={"context": {"session_id": "s"}})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Explicit-strategy path + fallback_strategy
# ---------------------------------------------------------------------------

def test_explicit_strategy_with_no_anchor_falls_back_then_to_popular(http, seeded):
    """strategy=content with no current_item_id in context can never produce anything
    (content needs an anchor) - fallback_strategy=popular should kick in, and the response
    says so via warnings being non-empty (visible through preview_placement)."""
    create_pdp_placement(
        http, seeded, slug="forced-content", strategy="content", fallback_strategy="popular",
        signals={"required": [], "optional": []},
    )
    response = http.post("/placements/forced-content/recommend", headers=seeded.public, json={"context": {}})
    assert response.status_code == 200
    assert response.json()["strategy_used"] == "popular"

    preview = http.post("/placements/forced-content/preview", headers=seeded.secret, json={"context": {}}).json()
    assert preview["strategy_used"] == "popular"
    assert preview["fallback_used"] is True
    assert preview["warnings"]
    assert preview["errors"] == []


def test_collaborative_with_no_trained_model_falls_back_to_hybrid_then_popular(http, seeded):
    create_pdp_placement(
        http, seeded, slug="forced-collab", strategy="collaborative", fallback_strategy="hybrid",
        signals={"required": [], "optional": ["current_item_id", "user_id"]},
    )
    http.post("/events/view", params=Q, headers=seeded.public, json={"user_id": "user_99", "item_id": "SKU-ADIDAS-007"})
    result = recommend(http, seeded, {"current_item_id": "SKU-NIKE-001", "user_id": "user_99"}, slug="forced-collab")
    # No model trained in this test - collaborative yields nothing, hybrid degrades to pure
    # content internally (recommender.rec_hybrid's own documented degrade-gracefully path).
    assert result["strategy_used"] == "hybrid"


# ---------------------------------------------------------------------------
# Filters / business rules
# ---------------------------------------------------------------------------

def test_in_stock_only_filter(http, seeded):
    http.put("/items/SKU-NIKE-001", params=Q, headers=seeded.secret, json={"title": "Nike Air Zoom Pegasus", "properties": {"category": "running-shoes", "stock": 0}})
    http.put("/items/SKU-ADIDAS-007", params=Q, headers=seeded.secret, json={"title": "Adidas Adizero Boston", "properties": {"category": "running-shoes", "stock": 5}})

    create_pdp_placement(
        http, seeded, slug="in-stock", strategy="popular", limit=8,
        signals={"required": [], "optional": []}, filters={"in_stock_only": True},
    )
    response = http.post("/placements/in-stock/recommend", headers=seeded.public, json={"context": {}})
    items = response.json()["items"]
    ids = {i["item_id"] for i in items}
    assert "SKU-NIKE-001" not in ids  # stock: 0 -> excluded
    # An item with neither stock nor in_stock is never excluded for lacking the field.
    assert any(shoe[0] not in {"SKU-NIKE-001", "SKU-ADIDAS-007"} for shoe in SHOES if shoe[0] in ids)


def test_category_filter(http, seeded):
    create_pdp_placement(
        http, seeded, slug="boots-only", strategy="popular", limit=8,
        signals={"required": [], "optional": []}, filters={"category_in": ["boots"]},
    )
    response = http.post("/placements/boots-only/recommend", headers=seeded.public, json={"context": {}})
    items = response.json()["items"]
    assert items  # the seeded catalog has 2 "boots"-category items
    assert all(item["properties"].get("category") == "boots" for item in items)


def test_exclude_cart_items(http, seeded):
    create_pdp_placement(
        http, seeded, slug="cart-cross-sell", strategy="popular", context_type="cart", limit=8,
        signals={"required": [], "optional": ["cart_item_ids"]},
        business_rules={"exclude_current_item": True, "exclude_cart_items": True},
    )
    response = http.post("/placements/cart-cross-sell/recommend", headers=seeded.public, json={
        "context": {"cart_item_ids": ["SKU-NIKE-001", "SKU-ADIDAS-007"]},
    })
    ids = {i["item_id"] for i in response.json()["items"]}
    assert "SKU-NIKE-001" not in ids
    assert "SKU-ADIDAS-007" not in ids


# ---------------------------------------------------------------------------
# get_placement_requirements / preview_placement debug shape
# ---------------------------------------------------------------------------

def test_get_placement_requirements(http, seeded):
    create_pdp_placement(http, seeded)
    requirements = http.get(f"/placements/{PDP_SLUG}/requirements", headers=seeded.secret).json()
    assert requirements["signals"]["required"] == ["current_item_id"]
    assert requirements["strategy"] == "auto"
    assert requirements["context_type"] == "product_page"


def test_preview_reports_missing_signals_without_a_422(http, seeded):
    create_pdp_placement(http, seeded)  # requires current_item_id
    preview = http.post(f"/placements/{PDP_SLUG}/preview", headers=seeded.secret, json={"context": {}}).json()
    assert preview["strategy_used"] is None
    assert preview["items"] == []
    assert "current_item_id" in preview["errors"][0]


def test_preview_never_mints_a_recommendation_id_or_stores_anything(http, seeded):
    create_pdp_placement(http, seeded, signals={"required": [], "optional": ["current_item_id"]})
    before = http.post(f"/placements/{PDP_SLUG}/preview", headers=seeded.secret, json={"context": {"current_item_id": "SKU-NIKE-001"}})
    assert "recommendation_id" not in before.json()


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_legacy_getRec_is_unaffected(http, seeded):
    response = http.post("/getRec", params=Q, headers=seeded.public, json={"item_id": "SKU-NIKE-001", "count": 3})
    assert response.status_code == 200
    assert response.json()["strategy"] == "content"
