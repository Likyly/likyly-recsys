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


def test_filters_never_leave_the_placement_short(http, seeded):
    """The current product's closest neighbours are out of stock: the placement still shows
    `limit` items - more candidates are ranked, then popular ones top it up."""
    for item_id, title, description, category in SHOES:
        stock = 0 if item_id in {"SKU-ADIDAS-007", "SKU-ASICS-003", "SKU-SALOMON-010"} else 5
        http.put(f"/items/{item_id}", params=Q, headers=seeded.secret, json={
            "title": title, "description": description, "properties": {"category": category, "stock": stock},
        })
    create_pdp_placement(http, seeded, slug="pdp-stock", strategy="content", limit=4, filters={"in_stock_only": True})

    for route in ("recommend", "preview"):
        headers = seeded.public if route == "recommend" else seeded.secret
        response = http.post(f"/placements/pdp-stock/{route}", headers=headers, json={"context": {"current_item_id": "SKU-NIKE-001", "session_id": "s-1"}})
        assert response.status_code == 200, response.text
        ids = [i["item_id"] for i in response.json()["items"]]
        # 8 items - the current one - 3 out of stock = exactly 4 left, all of them shown.
        assert len(ids) == 4, (route, ids)
        assert len(set(ids)) == 4
        assert not {"SKU-NIKE-001", "SKU-ADIDAS-007", "SKU-ASICS-003", "SKU-SALOMON-010"} & set(ids)


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


def test_a_signed_in_visitor_is_never_offered_back_what_she_bought(http, seeded):
    """Content and hybrid rank by similarity: without the rule, a customer on a product page
    gets her own purchases first (seen on the Magento sandbox). Default on; false keeps them."""
    create_pdp_placement(http, seeded, signals={"required": [], "optional": ["current_item_id", "user_id"]}, limit=7)
    bought = ["SKU-ADIDAS-007", "SKU-ASICS-003"]
    for item in bought:
        response = http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": "buyer_1", "item_id": item})
        assert response.status_code in (200, 201, 202), response.text
    ids = [i["item_id"] for i in recommend(http, seeded, {"current_item_id": "SKU-NIKE-001", "user_id": "buyer_1"})["items"]]
    assert ids and not set(bought) & set(ids)
    assert len(ids) == 5  # 8 items - the current one - the 2 bought: still a full list of what is left
    # Someone else, or a visitor who isn't signed in, still gets them.
    anonymous = [i["item_id"] for i in recommend(http, seeded, {"current_item_id": "SKU-NIKE-001"})["items"]]
    assert set(bought) <= set(anonymous)
    # Products bought again and again: the rule can be turned off.
    http.patch(f"/placements/{PDP_SLUG}", headers=seeded.secret, json={"business_rules": {"exclude_purchased_items": False}})
    again = [i["item_id"] for i in recommend(http, seeded, {"current_item_id": "SKU-NIKE-001", "user_id": "buyer_1"})["items"]]
    assert set(bought) <= set(again)


def test_also_bought_ranks_what_the_buyers_of_this_product_bought(http, seeded):
    """"Customers who bought this also bought": the items bought by the buyers of the current
    product, most buyers first, topped up by the most similar ones; a signed-in customer is not
    offered what she bought; a product nobody bought anything with falls back."""
    create_pdp_placement(http, seeded, slug="also-bought", strategy="also_bought", fallback_strategy="content", limit=4,
                         signals={"required": ["current_item_id"], "optional": ["user_id", "session_id"]})
    baskets = {
        "b1": ["SKU-NIKE-001", "SKU-VANS-004"],
        "b2": ["SKU-NIKE-001", "SKU-VANS-004", "SKU-ASICS-003"],
        "b3": ["SKU-NIKE-001", "SKU-CONVERSE-002"],
        "b4": ["SKU-SALOMON-010", "SKU-ASICS-003"],
    }
    for buyer, items in baskets.items():
        for item in items:
            assert http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": buyer, "item_id": item}).status_code < 300
    # An anonymous order counts too: its session is the buyer.
    for item in ["SKU-NIKE-001", "SKU-VANS-004"]:
        http.post("/events/purchase", params=Q, headers=seeded.public, json={"session_id": "order_77", "item_id": item})

    result = recommend(http, seeded, {"current_item_id": "SKU-NIKE-001"}, slug="also-bought")
    ids = [i["item_id"] for i in result["items"]]
    assert result["strategy_used"] == "also_bought"
    assert ids[0] == "SKU-VANS-004"  # 3 buyers of the Nike bought it (b1, b2, the anonymous order)
    assert set(ids[1:3]) == {"SKU-ASICS-003", "SKU-CONVERSE-002"}  # one buyer each
    assert len(ids) == 4 and "SKU-NIKE-001" not in ids  # topped up by content, never the product itself
    assert "SKU-SALOMON-010" not in ids[:3]  # bought by someone who did not buy the Nike

    # b2 bought the Vans and the Asics: she is offered the rest.
    mine = [i["item_id"] for i in recommend(http, seeded, {"current_item_id": "SKU-NIKE-001", "user_id": "b2"}, slug="also-bought")["items"]]
    assert "SKU-VANS-004" not in mine and "SKU-ASICS-003" not in mine and mine[0] == "SKU-CONVERSE-002"

    # Nothing bought with it: the placement's fallback answers, and says so.
    lonely = recommend(http, seeded, {"current_item_id": "SKU-ADIDAS-007"}, slug="also-bought")
    assert lonely["strategy_used"] == "content"


def test_cart_cross_sells_what_was_bought_with_the_cart(http, seeded):
    """A cart page (cart_item_ids, no current item) with strategy auto: first what the buyers of
    the cart's products also bought, then items close to the cart; never a product of the cart."""
    create_pdp_placement(http, seeded, slug="cart", context_type="cart", strategy="auto", limit=4,
                         signals={"required": [], "optional": ["cart_item_ids", "session_id"]},
                         business_rules={"exclude_cart_items": True})
    for buyer, items in {"b1": ["SKU-NIKE-001", "SKU-VANS-004"], "b2": ["SKU-ASICS-003", "SKU-VANS-004"], "b3": ["SKU-NIKE-001", "SKU-CONVERSE-002"]}.items():
        for item in items:
            http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": buyer, "item_id": item})

    cart = ["SKU-NIKE-001", "SKU-ASICS-003"]
    result = recommend(http, seeded, {"cart_item_ids": cart, "session_id": "s-cart"}, slug="cart")
    ids = [i["item_id"] for i in result["items"]]
    assert result["strategy_used"] == "also_bought"
    assert ids[0] == "SKU-VANS-004"  # bought by 2 buyers of the cart's products
    assert "SKU-CONVERSE-002" in ids and not set(cart) & set(ids)

    # Nothing bought with it yet: items close to the cart, still never the cart's own.
    lonely = recommend(http, seeded, {"cart_item_ids": ["SKU-SALOMON-010"], "session_id": "s-cart-2"}, slug="cart")
    assert lonely["strategy_used"] == "session"
    assert "SKU-SALOMON-010" not in [i["item_id"] for i in lonely["items"]]


def test_pinned_products_come_first_through_the_same_rules(http, seeded):
    """business_rules.pinned_item_ids: the merchant's products put forward lead, in their order;
    the current item, the cart and unknown ids are left out; LIKYLY's choice fills the rest."""
    create_pdp_placement(http, seeded, slug="pinned", business_rules={
        "exclude_current_item": True, "exclude_cart_items": True,
        "pinned_item_ids": ["SKU-SALOMON-010", "SKU-NIKE-001", "SKU-NOPE-999", "SKU-VANS-004", "SKU-ADIDAS-007"],
    })
    result = recommend(http, seeded, {"current_item_id": "SKU-NIKE-001", "cart_item_ids": ["SKU-VANS-004"]}, slug="pinned")
    ids = [i["item_id"] for i in result["items"]]
    assert ids[:2] == ["SKU-SALOMON-010", "SKU-ADIDAS-007"]  # pinned, in order; Nike is the page, Vans in the cart
    assert len(ids) == 4 and len(set(ids)) == 4 and "SKU-NIKE-001" not in ids and "SKU-VANS-004" not in ids
    assert result["items"][0]["explanation"]["reason"] == "Mis en avant par la boutique"

    too_many = http.patch("/placements/pinned", headers=seeded.secret, json={"business_rules": {"pinned_item_ids": [f"S{i}" for i in range(13)]}})
    assert too_many.status_code == 422


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


def test_a_placement_only_targets_a_catalog_the_account_has_and_can_be_moved(http, seeded):
    # Production: a placement created on a catalog deleted from the console answered with
    # nothing, and nothing said why.
    refused = http.post("/placements", headers=seeded.secret, json={
        "slug": "pdp-old", "name": "Old", "context_type": "product_page", "product_type": "running-shoes",
    })
    assert refused.status_code == 422
    assert "No catalog 'running-shoes'" in refused.json()["detail"] and "'shop'" in refused.json()["detail"]

    create_pdp_placement(http, seeded)
    assert http.patch(f"/placements/{PDP_SLUG}", headers=seeded.secret, json={"product_type": "gone"}).status_code == 422
    assert http.patch(f"/placements/{PDP_SLUG}", headers=seeded.secret, json={"product_type": "shop", "name": "Reused"}).json()["name"] == "Reused"


# ---------------------------------------------------------------------------
# A product page whose product LIKYLY doesn't have: nothing, not unrelated popular items
# ---------------------------------------------------------------------------

def test_unknown_current_item_on_a_product_page_returns_nothing(http, seeded):
    create_pdp_placement(http, seeded)
    response = http.post(f"/placements/{PDP_SLUG}/recommend", headers=seeded.public, json={"context": {"current_item_id": "GOLF-001"}})
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["strategy_used"] == "none"
    assert body["recommendation_id"]

    preview = http.post(f"/placements/{PDP_SLUG}/preview", headers=seeded.secret, json={"context": {"current_item_id": "GOLF-001"}}).json()
    assert preview["items"] == [] and preview["strategy_used"] == "none"
    assert "GOLF-001" in preview["warnings"][0] and "unknown_current_item" in preview["warnings"][0]

    # A product the catalog has still gets its neighbours.
    known = http.post(f"/placements/{PDP_SLUG}/recommend", headers=seeded.public, json={"context": {"current_item_id": "SKU-NIKE-001"}}).json()
    assert known["items"] and known["strategy_used"] != "none"


def test_unknown_current_item_popular_keeps_the_old_behaviour(http, seeded):
    create_pdp_placement(http, seeded, business_rules={"exclude_current_item": True, "unknown_current_item": "popular"})
    body = http.post(f"/placements/{PDP_SLUG}/recommend", headers=seeded.public, json={"context": {"current_item_id": "GOLF-001"}}).json()
    assert body["items"] and body["strategy_used"] == "popular"


def test_unknown_current_item_off_a_product_page_is_not_emptied(http, seeded):
    create_pdp_placement(http, seeded, context_type="homepage", signals={"required": [], "optional": ["current_item_id"]})
    body = http.post(f"/placements/{PDP_SLUG}/recommend", headers=seeded.public, json={"context": {"current_item_id": "GOLF-001"}}).json()
    assert body["items"]
