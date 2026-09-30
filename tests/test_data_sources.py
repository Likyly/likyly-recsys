"""Data source CRUD, tenant isolation, and the full create -> preview -> map -> sync flow
against a monkeypatched RestApiConnector (no real network in tests, same technique
conftest.py already uses for the embedding model)."""
import requests

import db
from conftest import make_tenant


class FakeResponse:
    def __init__(self, payload, status_code=200, links=None):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.links = links or {}
        self.text = str(payload)

    def json(self):
        return self._payload


REST_PRODUCTS = [
    {"id": "p1", "name": "Nike Air Zoom", "price": "129.90", "category": "running", "image": "http://x/1.jpg", "stock": 5},
    {"id": "p2", "name": "Adidas Boston", "price": "159.00", "category": "running", "image": "http://x/2.jpg", "stock": 0},
]


def _fake_get_single_page(url, params=None, headers=None, auth=None, timeout=None):
    return FakeResponse({"products": REST_PRODUCTS})


def test_create_data_source_requires_type_and_product_type(http, tenant):
    response = http.post("/data-sources", headers=tenant.secret, json={"name": "x", "type": "not-a-type", "product_type": "shop"})
    assert response.status_code == 422


def test_shopify_requires_credentials(http, tenant):
    response = http.post("/data-sources", headers=tenant.secret, json={
        "name": "shop", "type": "shopify", "product_type": "shop",
        "config": {"shop_domain": "x.myshopify.com"},
    })
    assert response.status_code == 422


def test_tenant_isolation_on_every_route(http, tenant, other_tenant):
    created = http.post("/data-sources", headers=tenant.secret, json={
        "name": "mine", "type": "rest_api", "product_type": "shop", "config": {"base_url": "https://example.invalid/products"},
    }).json()
    ds_id = created["id"]

    for method, path in [
        ("get", f"/data-sources/{ds_id}"),
        ("patch", f"/data-sources/{ds_id}"),
        ("delete", f"/data-sources/{ds_id}"),
        ("post", f"/data-sources/{ds_id}/test"),
        ("post", f"/data-sources/{ds_id}/preview"),
        ("get", f"/data-sources/{ds_id}/syncs"),
        ("get", f"/data-sources/{ds_id}/stats"),
    ]:
        kwargs = {"headers": other_tenant.secret}
        if method in ("patch", "post"):
            kwargs["json"] = {}
        response = getattr(http, method)(path, **kwargs)
        assert response.status_code == 404, f"{method} {path} leaked across tenants: {response.status_code}"

    # other_tenant's own listing must never include tenant's source
    assert created["id"] not in [d["id"] for d in http.get("/data-sources", headers=other_tenant.secret).json()]


def test_preview_field_mapping_and_full_sync(http, tenant, monkeypatch):
    monkeypatch.setattr("connectors.rest_api.RestApiConnector._request", lambda self, params: _fake_get_single_page(None))

    created = http.post("/data-sources", headers=tenant.secret, json={
        "name": "rest", "type": "rest_api", "product_type": "shop",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"}, "sync_mode": "full",
    }).json()
    ds_id = created["id"]
    assert created["sync_mode"] == "full"

    test_result = http.post(f"/data-sources/{ds_id}/test", headers=tenant.secret).json()
    assert test_result["ok"] is True

    preview = http.post(f"/data-sources/{ds_id}/preview", headers=tenant.secret).json()
    assert preview["sample"] == REST_PRODUCTS
    assert preview["suggested_mapping"]["external_id"] == "id"
    assert preview["suggested_mapping"]["title"] == "name"

    mapping = {"external_id": "id", "title": "name", "price": "price", "category": "category", "image": "image", "stock": "stock"}
    dry_run = http.post(f"/data-sources/{ds_id}/field-mapping/dry-run", headers=tenant.secret, json={"mapping": mapping}).json()
    assert len(dry_run["normalized_sample"]) == 2
    assert not dry_run["errors"]
    # not persisted yet
    assert http.get(f"/data-sources/{ds_id}", headers=tenant.secret).json()["field_mapping"] is None

    saved = http.put(f"/data-sources/{ds_id}/field-mapping", headers=tenant.secret, json={"mapping": mapping}).json()
    assert saved["field_mapping"] == mapping

    sync = http.post(f"/data-sources/{ds_id}/sync", headers=tenant.secret, json={"mode": "full"})
    assert sync.status_code == 202
    run = sync.json()
    assert run["status"] in ("running", "success")  # TestClient runs the background task inline

    runs = http.get(f"/data-sources/{ds_id}/syncs", headers=tenant.secret).json()
    assert runs[0]["status"] == "success"
    assert runs[0]["items_fetched"] == 2
    assert runs[0]["items_upserted"] == 2

    stats = http.get(f"/data-sources/{ds_id}/stats", headers=tenant.secret).json()
    assert stats["item_count"] == 2

    items = http.get("/items", params={"data_product_type": "shop"}, headers=tenant.secret).json()
    assert {i["item_id"] for i in items} == {"p1", "p2"}
    nike = next(i for i in items if i["item_id"] == "p1")
    assert nike["properties"]["price"] == 129.90
    assert nike["properties"]["image"] == "http://x/1.jpg"
    assert nike["properties"]["stock"] == 5


def test_idempotent_second_sync_upserts_nothing_unchanged(http, tenant, monkeypatch):
    calls = {"n": 0}

    def counting_upsert(client_id, product_type, entries):
        calls["n"] += len(entries)
        return real_upsert(client_id, product_type, entries)

    import ingestion
    real_upsert = ingestion.upsert_items_batch
    monkeypatch.setattr("connectors.rest_api.RestApiConnector._request", lambda self, params: _fake_get_single_page(None))
    monkeypatch.setattr("sync_engine.upsert_items_batch", counting_upsert)
    monkeypatch.setattr("db.SYNC_COOLDOWN_SECONDS", 0)  # two back-to-back syncs, not what's under test here

    created = http.post("/data-sources", headers=tenant.secret, json={
        "name": "rest", "type": "rest_api", "product_type": "shop2",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"}, "sync_mode": "full",
    }).json()
    ds_id = created["id"]
    mapping = {"external_id": "id", "title": "name", "price": "price"}
    http.put(f"/data-sources/{ds_id}/field-mapping", headers=tenant.secret, json={"mapping": mapping})

    http.post(f"/data-sources/{ds_id}/sync", headers=tenant.secret, json={"mode": "full"})
    assert calls["n"] == 2  # first sync: both items are new

    http.post(f"/data-sources/{ds_id}/sync", headers=tenant.secret, json={"mode": "full"})
    assert calls["n"] == 2  # second sync: unchanged - content hash skip, no new upsert calls

    runs = http.get(f"/data-sources/{ds_id}/syncs", headers=tenant.secret).json()
    assert runs[0]["items_upserted"] == 0
    assert runs[0]["items_fetched"] == 2


def test_deletion_tombstone_on_full_sync_only(http, tenant, monkeypatch):
    page = {"products": list(REST_PRODUCTS)}
    monkeypatch.setattr("connectors.rest_api.RestApiConnector._request", lambda self, params: FakeResponse({"products": page["products"]}))
    monkeypatch.setattr("db.SYNC_COOLDOWN_SECONDS", 0)  # two back-to-back syncs, not what's under test here

    created = http.post("/data-sources", headers=tenant.secret, json={
        "name": "rest", "type": "rest_api", "product_type": "shop3",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"}, "sync_mode": "full",
    }).json()
    ds_id = created["id"]
    mapping = {"external_id": "id", "title": "name", "price": "price"}
    http.put(f"/data-sources/{ds_id}/field-mapping", headers=tenant.secret, json={"mapping": mapping})

    http.post(f"/data-sources/{ds_id}/sync", headers=tenant.secret, json={"mode": "full"})
    items = http.get("/items", params={"data_product_type": "shop3"}, headers=tenant.secret).json()
    assert {i["item_id"] for i in items} == {"p1", "p2"}

    page["products"] = [REST_PRODUCTS[0]]  # p2 is now gone from the source
    http.post(f"/data-sources/{ds_id}/sync", headers=tenant.secret, json={"mode": "full"})

    items = http.get("/items", params={"data_product_type": "shop3"}, headers=tenant.secret).json()
    assert {i["item_id"] for i in items} == {"p1"}
    runs = http.get(f"/data-sources/{ds_id}/syncs", headers=tenant.secret).json()
    assert runs[0]["items_deleted"] == 1


def test_partial_failure_does_not_abort_the_run(http, tenant, monkeypatch):
    bad_page = {"products": [
        {"id": "ok1", "name": "Fine Item"},
        {"name": "Missing id"},  # no "id" -> normalize_item raises ValueError, counted not fatal
        {"id": "ok2", "name": "Also Fine"},
    ]}
    monkeypatch.setattr("connectors.rest_api.RestApiConnector._request", lambda self, params: FakeResponse(bad_page))

    created = http.post("/data-sources", headers=tenant.secret, json={
        "name": "rest", "type": "rest_api", "product_type": "shop4",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"}, "sync_mode": "full",
    }).json()
    ds_id = created["id"]
    http.put(f"/data-sources/{ds_id}/field-mapping", headers=tenant.secret, json={"mapping": {"external_id": "id", "title": "name"}})

    http.post(f"/data-sources/{ds_id}/sync", headers=tenant.secret, json={"mode": "full"})
    runs = http.get(f"/data-sources/{ds_id}/syncs", headers=tenant.secret).json()
    assert runs[0]["status"] == "partial"
    assert runs[0]["items_failed"] == 1
    assert runs[0]["items_upserted"] == 2

    items = http.get("/items", params={"data_product_type": "shop4"}, headers=tenant.secret).json()
    assert {i["item_id"] for i in items} == {"ok1", "ok2"}


def test_invalid_credentials_surface_a_clear_error_and_error_status(http, tenant, monkeypatch):
    def raise_auth_error(self, params):
        raise requests.RequestException("boom")

    monkeypatch.setattr("connectors.rest_api.RestApiConnector._request", raise_auth_error)

    created = http.post("/data-sources", headers=tenant.secret, json={
        "name": "rest", "type": "rest_api", "product_type": "shop5",
        "config": {"base_url": "https://example.invalid/products"},
    }).json()
    ds_id = created["id"]

    result = http.post(f"/data-sources/{ds_id}/test", headers=tenant.secret).json()
    assert result["ok"] is False

    status = http.get(f"/data-sources/{ds_id}", headers=tenant.secret).json()
    assert status["status"] == "error"
    assert status["last_error"]


def test_incremental_sync_rejected_for_full_only_types(http, tenant):
    created = http.post("/data-sources", headers=tenant.secret, json={
        "name": "csv", "type": "csv_url", "product_type": "shop6",
        "config": {"url": "https://example.invalid/x.csv", "format": "csv"},
    }).json()
    response = http.post(f"/data-sources/{created['id']}/sync", headers=tenant.secret, json={"mode": "incremental"})
    # falls back to full rather than erroring - see data_source_service.trigger_sync
    assert response.status_code == 202
    assert response.json()["mode"] == "full"


def test_woocommerce_and_webhook_sources_cannot_be_synced_directly(http, tenant):
    for type_, config in [("woocommerce", {}), ("webhook", {})]:
        created = http.post("/data-sources", headers=tenant.secret, json={
            "name": type_, "type": type_, "product_type": f"push-{type_}", "config": config,
        }).json()
        assert created["sync_mode"] == "push"
        response = http.post(f"/data-sources/{created['id']}/sync", headers=tenant.secret, json={})
        assert response.status_code == 422


def test_webhook_push_ingress(http, tenant):
    created = http.post("/data-sources", headers=tenant.secret, json={
        "name": "wh", "type": "webhook", "product_type": "pushcat", "config": {},
    }).json()
    ds_id = created["id"]
    push_secret = created["push_secret"]
    assert push_secret
    http.put(f"/data-sources/{ds_id}/field-mapping", headers=tenant.secret, json={"mapping": {"external_id": "id", "title": "name"}})

    response = http.post(f"/data-sources/{ds_id}/push", headers={"X-Push-Secret": push_secret}, json={
        "items": [{"id": "w1", "name": "Pushed Item"}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["upserted"] == 1

    items = http.get("/items", params={"data_product_type": "pushcat"}, headers=tenant.secret).json()
    assert {i["item_id"] for i in items} == {"w1"}

    wrong_secret = http.post(f"/data-sources/{ds_id}/push", headers={"X-Push-Secret": "nope"}, json={"items": []})
    assert wrong_secret.status_code == 404


def test_sync_cooldown_blocks_immediate_retrigger(http, tenant, monkeypatch):
    # "On ne peut pas ingérer un catalogue toutes les minutes" - a re-sync of the same source
    # inside db.SYNC_COOLDOWN_SECONDS of the last one must be rejected (429), not queued.
    monkeypatch.setattr("connectors.rest_api.RestApiConnector._request", lambda self, params: _fake_get_single_page(None))

    created = http.post("/data-sources", headers=tenant.secret, json={
        "name": "rest", "type": "rest_api", "product_type": "shop-cooldown",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"}, "sync_mode": "full",
    }).json()
    ds_id = created["id"]
    http.put(f"/data-sources/{ds_id}/field-mapping", headers=tenant.secret, json={"mapping": {"external_id": "id", "title": "name"}})

    first = http.post(f"/data-sources/{ds_id}/sync", headers=tenant.secret, json={"mode": "full"})
    assert first.status_code == 202

    retry = http.post(f"/data-sources/{ds_id}/sync", headers=tenant.secret, json={"mode": "full"})
    assert retry.status_code == 429
    assert "wait" in retry.json()["detail"].lower()

    # A different data source (even for the same tenant) has its own cooldown - unaffected.
    other = http.post("/data-sources", headers=tenant.secret, json={
        "name": "rest2", "type": "rest_api", "product_type": "shop-cooldown-2",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"}, "sync_mode": "full",
    }).json()
    http.put(f"/data-sources/{other['id']}/field-mapping", headers=tenant.secret, json={"mapping": {"external_id": "id", "title": "name"}})
    unaffected = http.post(f"/data-sources/{other['id']}/sync", headers=tenant.secret, json={"mode": "full"})
    assert unaffected.status_code == 202


def test_sync_daily_limit_on_free_plan(http, monkeypatch):
    monkeypatch.setattr("connectors.rest_api.RestApiConnector._request", lambda self, params: _fake_get_single_page(None))
    monkeypatch.setattr("db.SYNC_COOLDOWN_SECONDS", 0)  # isolate the daily cap from the cooldown
    monkeypatch.setitem(db.PLAN_LIMITS[db.PLAN_FREE], "manual_sync_daily_limit", 2)

    free_tenant = make_tenant(db.PLAN_FREE)
    created = http.post("/data-sources", headers=free_tenant.secret, json={
        "name": "rest", "type": "rest_api", "product_type": "shop-freecap",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"}, "sync_mode": "full",
    }).json()
    ds_id = created["id"]
    http.put(f"/data-sources/{ds_id}/field-mapping", headers=free_tenant.secret, json={"mapping": {"external_id": "id", "title": "name"}})

    for _ in range(2):
        response = http.post(f"/data-sources/{ds_id}/sync", headers=free_tenant.secret, json={"mode": "full"})
        assert response.status_code == 202

    over_limit = http.post(f"/data-sources/{ds_id}/sync", headers=free_tenant.secret, json={"mode": "full"})
    assert over_limit.status_code == 429
    assert "plan limit" in over_limit.json()["detail"].lower()


def test_data_source_limit_on_free_plan(http):
    # Free plan: one catalog connection max - a second one is a 403 (plan cap), not queued.
    free_tenant = make_tenant(db.PLAN_FREE)
    first = http.post("/data-sources", headers=free_tenant.secret, json={
        "name": "rest", "type": "rest_api", "product_type": "shop-onecap",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"},
    })
    assert first.status_code == 201

    second = http.post("/data-sources", headers=free_tenant.secret, json={
        "name": "rest2", "type": "rest_api", "product_type": "shop-onecap-2",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"},
    })
    assert second.status_code == 403
    assert "plan limit" in second.json()["detail"].lower()

    # A Pro-plan tenant isn't bound by the free cap.
    pro_tenant = make_tenant(db.PLAN_PRO)
    for i in range(2):
        response = http.post("/data-sources", headers=pro_tenant.secret, json={
            "name": f"rest-{i}", "type": "rest_api", "product_type": f"shop-pro-{i}",
            "config": {"base_url": "https://example.invalid/products", "items_path": "products"},
        })
        assert response.status_code == 201


def test_list_my_data_sources_via_supabase_session(http, api):
    # GET /clients/me/data-sources mirrors GET /data-sources but is Supabase-session-gated
    # (the dashboard) instead of API-key-gated (a coding agent / MCP) - same rows, same
    # client_id, credentials never included either way.
    client_id, secret_key, _public_key = db.create_client_for_supabase_user("workspace-test", "supabase-uid-workspace-1")
    created = http.post("/data-sources", headers={"X-API-Key": secret_key}, json={
        "name": "rest", "type": "rest_api", "product_type": "shop-workspace",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"},
    }).json()

    unauthenticated = http.get("/clients/me/data-sources")
    assert unauthenticated.status_code == 401

    api.app.dependency_overrides[api.get_current_supabase_user_id] = lambda: "supabase-uid-workspace-1"
    try:
        response = http.get("/clients/me/data-sources")
    finally:
        api.app.dependency_overrides.pop(api.get_current_supabase_user_id, None)

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]
    assert rows[0]["type"] == "rest_api"
    assert "credentials_encrypted" not in rows[0]


def test_sync_my_data_source_via_supabase_session(http, api, monkeypatch):
    # POST/GET /clients/me/data-sources/{id}/sync(s) mirror the secret-key routes but are
    # Supabase-session-gated - the dashboard's "Synchroniser maintenant" button, no API key.
    monkeypatch.setattr("connectors.rest_api.RestApiConnector._request", lambda self, params: _fake_get_single_page(None))

    client_id, secret_key, _public_key = db.create_client_for_supabase_user("workspace-sync-test", "supabase-uid-sync-1")
    created = http.post("/data-sources", headers={"X-API-Key": secret_key}, json={
        "name": "rest", "type": "rest_api", "product_type": "shop-supabase-sync",
        "config": {"base_url": "https://example.invalid/products", "items_path": "products"}, "sync_mode": "full",
    }).json()
    ds_id = created["id"]
    http.put(f"/data-sources/{ds_id}/field-mapping", headers={"X-API-Key": secret_key}, json={"mapping": {"external_id": "id", "title": "name"}})

    unauthenticated = http.post(f"/clients/me/data-sources/{ds_id}/sync", json={"mode": "full"})
    assert unauthenticated.status_code == 401

    api.app.dependency_overrides[api.get_current_supabase_user_id] = lambda: "supabase-uid-sync-1"
    try:
        triggered = http.post(f"/clients/me/data-sources/{ds_id}/sync", json={"mode": "full"})
        assert triggered.status_code == 202
        run = triggered.json()
        assert run["status"] in ("running", "success")  # TestClient runs the background task inline

        runs = http.get(f"/clients/me/data-sources/{ds_id}/syncs").json()
        assert runs[0]["status"] == "success"
        assert runs[0]["items_upserted"] == 2

        # Someone else's data source id is invisible through this endpoint too - never a
        # cross-tenant leak, and never a cross-tenant trigger.
        _other_client_id, other_secret, _other_public = db.create_client_for_supabase_user("workspace-sync-other", "supabase-uid-sync-2")
        other_source = http.post("/data-sources", headers={"X-API-Key": other_secret}, json={
            "name": "rest-other", "type": "rest_api", "product_type": "shop-supabase-sync-other",
            "config": {"base_url": "https://example.invalid/products", "items_path": "products"},
        }).json()
        cross_tenant = http.post(f"/clients/me/data-sources/{other_source['id']}/sync", json={"mode": "full"})
        assert cross_tenant.status_code == 404
    finally:
        api.app.dependency_overrides.pop(api.get_current_supabase_user_id, None)
