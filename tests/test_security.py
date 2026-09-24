"""API key scopes and tenant isolation.

The public key ships inside web pages, so what it can do is a security boundary: it may
recommend and track events - nothing else. The secret key keeps full rights.
"""
import pytest

import db
from conftest import Q, interactions_of, make_tenant

ITEM = {"title": "T"}

# (method, path, json body) - every operation that WRITES or reads personal data or administers.
SECRET_ONLY = [
    ("PUT", "/items/SKU-NIKE-001", ITEM),
    ("DELETE", "/items/SKU-NIKE-001", None),
    ("POST", "/items/import", {"items": [{"item_id": "a", "title": "t"}]}),
    ("POST", "/items/delete", {"item_ids": ["a"]}),
    ("GET", "/users", None),
    ("GET", "/users/user_1", None),
    ("PUT", "/users/user_1", {"properties": {}}),
    ("DELETE", "/users/user_1", None),
    ("POST", "/users/import", {"users": [{"user_id": "a"}]}),
    ("GET", "/items", None),
    ("GET", "/items/SKU-NIKE-001", None),
    ("GET", "/generateModel", None),
    ("GET", "/generateModel/status/abc", None),
    ("GET", "/models/versions", None),
    ("GET", "/models/status", None),
    ("GET", "/getRec/contentVec/createIndex", None),
]

# Operations a browser may perform with the public key: recommend and track. Nothing else - in
# particular it can neither write NOR READ the catalog. Adding a route here is a security decision.
PUBLIC_OK = [
    ("POST", "/getRec", {"count": 3}),
    ("GET", "/getRec/popular/3", None),
    ("GET", "/getRec/content/SKU-NIKE-001/3", None),
    ("GET", "/getRec/collaborative/u/3", None),
    ("GET", "/getRec/hybrid/u/SKU-NIKE-001/3", None),
    ("GET", "/getRec/session?viewed_item_ids=SKU-NIKE-001", None),
    ("GET", "/getRec/sessionForUser/u/3", None),
    ("GET", "/getRec/contentVec/SKU-NIKE-001/3", None),
    ("POST", "/events/view", {"session_id": "s", "item_id": "SKU-NIKE-001"}),
    ("POST", "/events/purchase", {"session_id": "s", "item_id": "SKU-NIKE-001"}),
    ("POST", "/events/click", {"session_id": "s", "item_id": "SKU-NIKE-001"}),
    ("POST", "/events/batch", {"events": [{"event_type": "view", "session_id": "s", "item_id": "a"}]}),
]


def call(http, method, path, headers, body):
    sep = "&" if "?" in path else "?"
    return http.request(method, f"{path}{sep}data_product_type=shop", headers=headers, json=body)


@pytest.mark.parametrize("method,path,body", SECRET_ONLY, ids=[f"{m} {p}" for m, p, _ in SECRET_ONLY])
def test_public_key_is_refused_on_writes_users_and_admin(http, seeded, method, path, body):
    response = call(http, method, path, seeded.public, body)
    assert response.status_code == 403, response.text
    assert "secret API key" in response.json()["detail"]


@pytest.mark.parametrize("method,path,body", SECRET_ONLY, ids=[f"{m} {p}" for m, p, _ in SECRET_ONLY])
def test_secret_key_is_not_refused_anywhere_the_public_key_is(http, seeded, method, path, body):
    """(Not every call succeeds - that's the operation's business - but never 401/403.)"""
    if path in ("/getRec/contentVec/createIndex", "/generateModel"):
        pytest.skip("triggers Pinecone / a training job")
    assert call(http, method, path, seeded.secret, body).status_code not in (401, 403)


@pytest.mark.parametrize("method,path,body", PUBLIC_OK, ids=[f"{m} {p}" for m, p, _ in PUBLIC_OK])
def test_public_key_can_recommend_and_track(http, seeded, method, path, body):
    if "contentVec" in path:
        pytest.skip("Pinecone")
    assert call(http, method, path, seeded.public, body).status_code not in (401, 403)


@pytest.mark.parametrize("method,path,body", SECRET_ONLY + PUBLIC_OK, ids=[f"{m} {p}" for m, p, _ in SECRET_ONLY + PUBLIC_OK])
def test_no_key_and_bad_key_are_401(http, method, path, body):
    assert call(http, method, path, {}, body).status_code == 401
    assert call(http, method, path, {"X-API-Key": "definitely-not-a-key"}, body).status_code == 401


def test_a_public_key_write_leaves_the_catalog_untouched(http, seeded):
    http.put("/items/SKU-NIKE-001", params=Q, headers=seeded.public, json={"title": "HACKED"})
    http.delete("/items/SKU-ADIDAS-007", params=Q, headers=seeded.public)
    assert http.get("/items/SKU-NIKE-001", params=Q, headers=seeded.secret).json()["title"] == "Nike Air Zoom Pegasus"
    assert http.get("/items/SKU-ADIDAS-007", params=Q, headers=seeded.secret).status_code == 200


def test_admin_and_account_routes_reject_api_keys_of_both_kinds(http, seeded):
    for headers in (seeded.secret, seeded.public):
        assert http.get("/admin/clients", headers=headers).status_code == 401
        assert http.delete(f"/admin/clients/{seeded.client_id}", headers=headers).status_code == 401
        assert http.post("/clients/me", headers=headers).status_code == 401
        assert http.get("/clients/me/usage", headers=headers).status_code == 401
        assert http.post("/clients/me/regenerate-secret-key", headers=headers).status_code == 401


def test_public_scope_matches_the_openapi_declaration(http):
    """The spec's x-required-key is read from the real auth dependencies; this pins the set of
    operations reachable with the public key so a new one can't slip in unnoticed."""
    schema = http.get("/openapi.json").json()
    declared_public = {
        (method.upper(), path) for path, operations in schema["paths"].items()
        for method, operation in operations.items() if operation.get("x-required-key") == "public"
    }
    allowed = {(m, p.split("?")[0].replace("SKU-NIKE-001", "{item_id}")) for m, p, _ in PUBLIC_OK}
    normalized = set()
    for method, path in declared_public:
        normalized.add((method, path))
    expected_paths = {
        ("POST", "/getRec"), ("GET", "/getRec/popular/{count}"), ("GET", "/getRec/content/{product_id}/{count}"),
        ("GET", "/getRec/collaborative/{user_id}/{count}"), ("GET", "/getRec/hybrid/{user_id}/{product_id}/{count}"),
        ("GET", "/getRec/session"), ("GET", "/getRec/sessionForUser/{user_id}/{count}"),
        ("GET", "/getRec/contentVec/{product_id}/{count}"),
        ("POST", "/events/view"), ("POST", "/events/purchase"), ("POST", "/events/batch"), ("POST", "/events/{event_type}"),
    }
    assert normalized == expected_paths
    assert allowed  # PUBLIC_OK covers each of the above


class TestTenantIsolation:
    def test_catalogs_are_invisible_across_tenants(self, http, seeded, other_tenant):
        assert http.get("/items", params=Q, headers=other_tenant.secret).json() == []
        assert http.get("/items/SKU-NIKE-001", params=Q, headers=other_tenant.secret).status_code == 404

    def test_same_external_id_in_two_tenants_are_independent_items(self, http, seeded, other_tenant):
        http.put("/items/SKU-NIKE-001", params=Q, headers=other_tenant.secret, json={"title": "Other tenant's shoe"})
        assert http.get("/items/SKU-NIKE-001", params=Q, headers=seeded.secret).json()["title"] == "Nike Air Zoom Pegasus"
        assert http.get("/items/SKU-NIKE-001", params=Q, headers=other_tenant.secret).json()["title"] == "Other tenant's shoe"
        http.delete("/items/SKU-NIKE-001", params=Q, headers=other_tenant.secret)
        assert http.get("/items/SKU-NIKE-001", params=Q, headers=seeded.secret).status_code == 200

    def test_users_and_their_events_are_isolated(self, http, seeded, other_tenant):
        http.put("/users/user_1", params=Q, headers=seeded.secret, json={"properties": {"secret": "A"}})
        http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": "user_1", "item_id": "SKU-NIKE-001"})
        assert http.get("/users/user_1", params=Q, headers=other_tenant.secret).status_code == 404
        assert http.delete("/users/user_1", params=Q, headers=other_tenant.secret).status_code == 404
        assert len(interactions_of(seeded)) == 1 and len(interactions_of(other_tenant)) == 0

    def test_recommendations_never_contain_another_tenants_items(self, http, seeded, other_tenant):
        http.put("/items/other-1", params=Q, headers=other_tenant.secret, json={"title": "Private", "description": "running shoe"})
        http.post("/events/purchase", params=Q, headers=other_tenant.public, json={"user_id": "u", "item_id": "other-1", "quantity": 50})
        for body in ({"count": 20}, {"item_id": "SKU-NIKE-001", "count": 20}, {"viewed_item_ids": ["SKU-NIKE-001"], "count": 20}):
            response = http.post("/getRec", params=Q, headers=seeded.public, json=body)
            assert "other-1" not in [i["item_id"] for i in response.json()["items"]]

    def test_recommendation_traces_and_ids_are_tenant_scoped(self, http, seeded, other_tenant):
        recommendation_id = http.post("/getRec", params=Q, headers=seeded.public, json={"count": 2}).json()["recommendation_id"]
        assert db.get_recommendation(seeded.client_id, recommendation_id) is not None
        assert db.get_recommendation(other_tenant.client_id, recommendation_id) is None

    def test_internal_ids_are_allocated_per_tenant(self, http, seeded, other_tenant):
        http.put("/items/a", params=Q, headers=other_tenant.secret, json={"title": "first ever"})
        with db.SessionLocal() as session:
            internal = session.query(db.IdMapModel).filter_by(client_id=other_tenant.client_id, kind="item", external_id="a").one().internal_id
        assert internal == 0  # dense per tenant, not a global counter shared with `seeded`

    def test_deleting_a_client_removes_its_mappings_and_traces(self, http):
        tenant = make_tenant()
        http.put("/items/a", params=Q, headers=tenant.secret, json={"title": "t"})
        http.post("/getRec", params=Q, headers=tenant.public, json={})
        assert db.delete_client(tenant.client_id)
        with db.SessionLocal() as session:
            assert session.query(db.IdMapModel).filter_by(client_id=tenant.client_id).count() == 0
            assert session.query(db.RecommendationModel).filter_by(client_id=tenant.client_id).count() == 0

    def test_disabled_client_keys_stop_working(self, http, seeded):
        db.set_client_active(seeded.client_id, False)
        try:
            assert http.post("/getRec", params=Q, headers=seeded.public, json={}).status_code == 401
        finally:
            db.set_client_active(seeded.client_id, True)
