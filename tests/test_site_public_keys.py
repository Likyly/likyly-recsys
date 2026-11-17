"""Site public keys: more public keys for one workspace, one per site or project, so giving a new
project a key never breaks the sites already live (regenerating the workspace's single public
key did - the wizard run for one project hid the blocks of another)."""
import uuid

import pytest

import app as app_module
import db
from conftest import Q

SLUG = "pdp-related"


@pytest.fixture
def auth(monkeypatch):
    user_id = f"supabase-user-site-keys-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": user_id, "email": "sites@example.com"})
    return {"Authorization": "Bearer fake"}


def key(raw: str) -> dict:
    return {"X-API-Key": raw}


def view(http, raw: str):
    return http.post("/events/product_view", params=Q, headers=key(raw), json={"session_id": "s1", "item_id": "SKU-NIKE-001"})


def create_placement(http, tenant):
    response = http.post("/placements", headers=tenant.secret, json={
        "slug": SLUG, "name": "Related products", "context_type": "product_page", "product_type": "shop",
        "limit": 4, "strategy": "auto", "signals": {"required": ["current_item_id"], "optional": ["session_id"]},
    })
    assert response.status_code == 201, response.text


def test_site_keys_work_side_by_side_with_the_workspace_key(http, seeded):
    _, first = db.create_site_public_key(seeded.client_id, "shop.example.com")
    _, second = db.create_site_public_key(seeded.client_id, "preview")
    assert first.startswith("pk_")
    for raw in (seeded.public_key, first, second):
        assert view(http, raw).status_code == 200


def test_a_site_key_has_the_public_scope_only(http, seeded):
    _, raw = db.create_site_public_key(seeded.client_id, "shop.example.com")
    assert http.get("/placements", headers=key(raw)).status_code == 403
    assert http.post("/items/import", params=Q, headers=key(raw), json={"items": []}).status_code == 403


def test_revoking_a_site_key_stops_only_that_one(http, seeded):
    row, revoked = db.create_site_public_key(seeded.client_id, "old site")
    _, kept = db.create_site_public_key(seeded.client_id, "new site")
    assert db.revoke_site_public_key(seeded.client_id, row["id"])
    assert view(http, revoked).status_code == 401
    assert view(http, kept).status_code == 200
    assert view(http, seeded.public_key).status_code == 200
    assert not db.revoke_site_public_key(seeded.client_id, row["id"])  # already revoked


def test_a_site_key_of_an_inactive_workspace_is_refused(http, seeded):
    _, raw = db.create_site_public_key(seeded.client_id, "shop.example.com")
    with db.SessionLocal() as session:
        session.query(db.ClientModel).filter_by(id=seeded.client_id).update({"is_active": False})
        session.commit()
    assert view(http, raw).status_code == 401


def test_the_console_routes_create_list_and_revoke(http, auth):
    http.post("/clients/me", headers=auth)
    created = http.post("/clients/me/public-keys", headers=auth, json={"name": "  dev-test3  "})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["name"] == "dev-test3"
    assert body["public_key"].startswith("pk_") and body["key_hint"] == f"{body['public_key'][:7]}…{body['public_key'][-4:]}"
    assert view(http, body["public_key"]).status_code == 200

    listed = http.get("/clients/me/public-keys", headers=auth).json()
    assert [k["id"] for k in listed] == [body["id"]]
    # A public key is public: it is listed with its value, for the console to put in the code it hands out.
    assert listed[0]["public_key"] == body["public_key"]

    assert http.delete(f"/clients/me/public-keys/{body['id']}", headers=auth).status_code == 200
    assert http.delete(f"/clients/me/public-keys/{body['id']}", headers=auth).status_code == 404
    revoked = http.get("/clients/me/public-keys", headers=auth).json()[0]
    assert revoked["revoked_at"] is not None
    assert revoked["public_key"] is None  # a revoked key is not handed out again
    assert view(http, body["public_key"]).status_code == 401


def test_another_workspace_cannot_revoke_a_key(http, auth, seeded):
    row, raw = db.create_site_public_key(seeded.client_id, "shop.example.com")
    http.post("/clients/me", headers=auth)
    assert http.delete(f"/clients/me/public-keys/{row['id']}", headers=auth).status_code == 404
    assert view(http, raw).status_code == 200


def test_the_number_of_active_site_keys_is_capped(http, auth, monkeypatch):
    monkeypatch.setattr(db, "MAX_SITE_PUBLIC_KEYS", 2)
    http.post("/clients/me", headers=auth)
    first = http.post("/clients/me/public-keys", headers=auth, json={"name": "a"}).json()
    http.post("/clients/me/public-keys", headers=auth, json={"name": "b"})
    refused = http.post("/clients/me/public-keys", headers=auth, json={"name": "c"})
    assert refused.status_code == 409 and "revoke" in refused.json()["detail"]
    http.delete(f"/clients/me/public-keys/{first['id']}", headers=auth)
    assert http.post("/clients/me/public-keys", headers=auth, json={"name": "c"}).status_code == 200


def test_health_flags_a_revoked_site_key_the_site_still_uses(http, seeded):
    create_placement(http, seeded)
    row, raw = db.create_site_public_key(seeded.client_id, "shop.example.com")
    context = {"context": {"current_item_id": "SKU-NIKE-001", "session_id": "s1"}}
    assert http.post(f"/placements/{SLUG}/recommend", headers=key(raw), json=context).status_code == 200
    db.revoke_site_public_key(seeded.client_id, row["id"])

    checks = {c["name"]: c for c in http.get(f"/placements/{SLUG}/health", headers=seeded.secret).json()["checks"]}
    assert checks["public_key_rotated"]["status"] == "missing"
    assert "revoked" in checks["public_key_rotated"]["message"]
