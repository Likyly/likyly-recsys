"""data_product_type is optional: explicit always wins; omitted resolves to the account's only
catalog ("default" for a fresh one); several catalogs must be named."""
import pytest

import app as app_module
import db
from conftest import make_tenant


@pytest.fixture(autouse=True)
def _fresh_catalog_cache():
    app_module._catalog_cache.clear()
    yield
    app_module._catalog_cache.clear()


def test_a_fresh_account_needs_no_catalog_parameter_anywhere(http):
    tenant = make_tenant()
    assert http.put("/items/SKU-1", headers=tenant.secret, json={"title": "Red shoe", "description": "running"}).status_code == 201
    assert http.post("/events/view", headers=tenant.public, json={"session_id": "s", "item_id": "SKU-1"}).status_code == 200
    assert db.list_catalogs_for_client(tenant.client_id) == ["default"]
    response = http.post("/getRec", headers=tenant.public, json={"count": 3})
    assert response.status_code == 200 and [i["item_id"] for i in response.json()["items"]] == ["SKU-1"]
    assert http.get("/items/SKU-1", headers=tenant.secret).json()["title"] == "Red shoe"
    # and "default" is just a name: passing it explicitly is the same catalog
    assert http.get("/items/SKU-1", params={"data_product_type": "default"}, headers=tenant.secret).status_code == 200


def test_an_account_with_one_existing_catalog_uses_it_when_omitted(http):
    """An integration that always said data_product_type=movies and forgets it does not silently
    read an empty "default" catalog."""
    tenant = make_tenant()
    http.put("/items/1", params={"data_product_type": "movies"}, headers=tenant.secret, json={"title": "Alien"})
    assert http.get("/items/1", headers=tenant.secret).json()["title"] == "Alien"
    assert http.get("/items", headers=tenant.secret).headers["x-total-count"] == "1"


def test_an_events_only_account_counts_as_having_that_catalog(http):
    tenant = make_tenant()
    http.post("/events/purchase", params={"data_product_type": "movies"}, headers=tenant.public, json={"user_id": "u", "item_id": "1"})
    http.post("/events/purchase", headers=tenant.public, json={"user_id": "u", "item_id": "2"})  # omitted -> movies
    assert db.list_catalogs_for_client(tenant.client_id) == ["movies"]


def test_several_catalogs_must_be_named_and_the_error_does_not_list_them(http):
    tenant = make_tenant()
    for catalog in ("shop", "blog"):
        http.put("/items/a", params={"data_product_type": catalog}, headers=tenant.secret, json={"title": catalog})
    response = http.get("/items/a", headers=tenant.secret)
    assert response.status_code == 422 and "several catalogs" in response.json()["detail"]
    assert "shop" not in response.text and "blog" not in response.text  # a public key must not learn catalog names
    assert http.post("/getRec", headers=tenant.public, json={}).status_code == 422
    assert http.get("/items/a", params={"data_product_type": "blog"}, headers=tenant.secret).json()["title"] == "blog"


def test_explicit_always_wins_even_with_one_catalog(http):
    tenant = make_tenant()
    http.put("/items/a", params={"data_product_type": "shop"}, headers=tenant.secret, json={"title": "in shop"})
    assert http.get("/items/a", params={"data_product_type": "other"}, headers=tenant.secret).status_code == 404


def test_authentication_still_comes_first(http):
    tenant = make_tenant()
    assert http.get("/items").status_code == 401
    assert http.get("/items", headers={"X-API-Key": "nope"}).status_code == 401
    assert http.get("/items", headers=tenant.public).status_code == 403  # public key on a secret route


def test_an_invalid_catalog_name_is_still_rejected(http):
    tenant = make_tenant()
    assert http.get("/items", params={"data_product_type": "bad name!"}, headers=tenant.secret).status_code == 422


def test_account_routes_authenticated_by_jwt_keep_the_parameter_required(http, api):
    """/clients/me/* are not API-key routes: no key to resolve a catalog from."""
    from fastapi.routing import APIRoute
    for route in api.app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/clients/me") and any(p.name == "data_product_type" for p in route.dependant.query_params):
            assert next(p for p in route.dependant.query_params if p.name == "data_product_type").required, route.path


def test_openapi_documents_the_parameter_as_optional(http):
    schema = http.get("/openapi.json").json()
    for path in ("/getRec", "/items", "/events/{event_type}"):
        for operation in schema["paths"][path].values():
            parameter = next(p for p in operation["parameters"] if p["name"] == "data_product_type")
            assert parameter.get("required") is False and "optional" in parameter["description"].lower()
