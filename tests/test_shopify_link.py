"""PUT/GET/DELETE /integrations/shopify: only the LIKYLY app for Shopify (its own secret) ties a
shop to a workspace; one shop -> one workspace, one workspace -> one shop."""
import pytest

from conftest import make_tenant

APP = {"X-Likyly-App-Secret": "app-secret-for-tests"}
SHOP = {"shop": "lola-bags.myshopify.com"}


@pytest.fixture(autouse=True)
def _app_secret(monkeypatch):
    monkeypatch.setenv("LIKYLY_SHOPIFY_APP_SECRET", APP["X-Likyly-App-Secret"])


def test_the_app_ties_a_shop_and_the_workspace_sees_it(http, tenant):
    response = http.put("/integrations/shopify", headers={**tenant.secret, **APP}, json=SHOP)
    assert response.status_code == 200, response.text
    assert response.json()["shop"] == SHOP["shop"]
    assert http.get("/integrations/shopify", headers=tenant.secret).json()["shop"] == SHOP["shop"]

    import db
    assert db.get_client_self_by_id(tenant.client_id)["shopify_shop"] == SHOP["shop"]
    assert db.get_client_admin_row(tenant.client_id)["shopify_shop"] == SHOP["shop"]


def test_a_merchant_key_alone_cannot_claim_a_shop(http, tenant):
    assert http.put("/integrations/shopify", headers=tenant.secret, json=SHOP).status_code == 403
    assert http.put("/integrations/shopify", headers={**tenant.secret, "X-Likyly-App-Secret": "guess"}, json=SHOP).status_code == 403
    assert http.put("/integrations/shopify", headers={**tenant.public, **APP}, json=SHOP).status_code == 403
    assert http.get("/integrations/shopify", headers=tenant.secret).status_code == 404


def test_not_configured_server_refuses(http, tenant, monkeypatch):
    monkeypatch.delenv("LIKYLY_SHOPIFY_APP_SECRET")
    assert http.put("/integrations/shopify", headers={**tenant.secret, **APP}, json=SHOP).status_code == 503


def test_only_shopify_domains(http, tenant):
    for shop in ["lola-bags.com", "evil.myshopify.com.example.org", "UPPER.myshopify.com", ""]:
        assert http.put("/integrations/shopify", headers={**tenant.secret, **APP}, json={"shop": shop}).status_code == 422, shop


def test_one_shop_one_workspace(http, tenant):
    other = make_tenant()
    http.put("/integrations/shopify", headers={**tenant.secret, **APP}, json=SHOP)
    # The merchant reconnects the shop with another account: the shop moves there.
    assert http.put("/integrations/shopify", headers={**other.secret, **APP}, json=SHOP).status_code == 200
    assert http.get("/integrations/shopify", headers=tenant.secret).status_code == 404
    assert http.get("/integrations/shopify", headers=other.secret).json()["shop"] == SHOP["shop"]
    # A workspace ties one shop: a new one replaces the previous.
    http.put("/integrations/shopify", headers={**other.secret, **APP}, json={"shop": "second.myshopify.com"})
    assert http.get("/integrations/shopify", headers=other.secret).json()["shop"] == "second.myshopify.com"


def test_untie_is_idempotent(http, tenant):
    http.put("/integrations/shopify", headers={**tenant.secret, **APP}, json=SHOP)
    assert http.delete("/integrations/shopify", headers=tenant.secret).json()["message"] == "Shopify shop untied"
    assert http.delete("/integrations/shopify", headers=tenant.secret).json()["message"] == "No Shopify shop was tied"
    assert http.get("/integrations/shopify", headers=tenant.secret).status_code == 404


# --- One-click connection: approved on likyly.com, claimed by the app ----------------------------

import uuid  # noqa: E402

import app as app_module  # noqa: E402

STATE = "s" * 40


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """Each test its own one-time state (approvals stay in the database between tests)."""
    global STATE
    STATE = uuid.uuid4().hex + uuid.uuid4().hex


@pytest.fixture
def merchant(monkeypatch, http):
    uid = f"supabase-user-shopify-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": uid, "email": "merchant@example.com"})
    auth = {"Authorization": "Bearer fake"}
    created = http.post("/clients/me", headers=auth).json()
    return {"auth": auth, "client_id": created["client_id"], "secret": {"X-API-Key": created["secret_key"]}}


def _claim(http, state=None, shop=SHOP["shop"], headers=APP):
    return http.post("/integrations/shopify/claim", headers=headers, json={"shop": shop, "state": state or STATE})


def test_one_click_connection(http, merchant):
    assert _claim(http).json() == {"status": "pending", "public_key": None, "secret_key": None, "workspace": None}
    approved = http.post("/clients/me/integrations/shopify/connect", headers=merchant["auth"], json={**SHOP, "state": STATE})
    assert approved.status_code == 200, approved.text
    assert approved.json()["shop"] == SHOP["shop"]

    claimed = _claim(http).json()
    assert claimed["status"] == "connected"
    assert claimed["public_key"].startswith("pk_") and claimed["secret_key"].startswith("sk_")
    # The shop is tied, and the app's own secret key works like the workspace's.
    app_key = {"X-API-Key": claimed["secret_key"]}
    assert http.get("/integrations/shopify", headers=app_key).json()["shop"] == SHOP["shop"]
    assert http.get("/integrations/shopify", headers=merchant["secret"]).json()["shop"] == SHOP["shop"]
    # The app ties the shop again at connection (PUT): the key it got stays valid.
    assert http.put("/integrations/shopify", headers={**app_key, **APP}, json=SHOP).status_code == 200
    assert http.get("/integrations/shopify", headers=app_key).status_code == 200
    # Once only.
    assert _claim(http).json()["status"] == "pending"
    # Untying the shop revokes the app's key; the workspace's own key is untouched.
    http.delete("/integrations/shopify", headers=merchant["secret"])
    assert http.get("/integrations/shopify", headers=app_key).status_code == 401
    assert http.get("/integrations/shopify", headers=merchant["secret"]).status_code == 404


def test_claim_needs_the_app_secret_the_same_shop_and_a_fresh_approval(http, merchant):
    http.post("/clients/me/integrations/shopify/connect", headers=merchant["auth"], json={**SHOP, "state": STATE})
    assert _claim(http, headers={}).status_code == 403
    assert _claim(http, headers={"X-Likyly-App-Secret": "guess"}).status_code == 403
    assert _claim(http, shop="other.myshopify.com").json()["status"] == "pending"
    import db
    from datetime import timedelta
    with db.SessionLocal() as session:
        session.query(db.PlatformConnectRequestModel).filter_by(state_hash=db.hash_api_key(STATE)).update({"created_at": db.utcnow() - timedelta(hours=1)})
        session.commit()
    assert _claim(http).json()["status"] == "pending"  # expired


def test_approval_needs_a_session(http):
    assert http.post("/clients/me/integrations/shopify/connect", json={**SHOP, "state": STATE}).status_code == 401


def test_approval_needs_a_real_state(http, merchant):
    assert http.post("/clients/me/integrations/shopify/connect", headers=merchant["auth"], json={**SHOP, "state": "short"}).status_code == 422
