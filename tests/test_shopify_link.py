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
