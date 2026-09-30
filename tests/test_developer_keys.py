"""Developer keys: a third credential kind for tenant-scoped tooling (Claude Code, Codex),
distinct from the secret/public keys - see db.DeveloperKeyModel / ALLOWED_SCOPES and app.py's
/clients/me/developer-keys* routes.

Self-service routes are Supabase-JWT authenticated; since conftest.py points SUPABASE_URL at
an unreachable domain (no real IdP in tests), _decode_supabase_jwt is monkeypatched here to
skip real JWKS verification - the same technique any test of the existing /clients/me* /
/admin/* routes would need, none of which happened to need it before this feature.
"""
import pytest

import app as app_module

SUPABASE_USER_ID = "supabase-user-devkeys-1"


@pytest.fixture
def supabase_auth(monkeypatch):
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": SUPABASE_USER_ID, "email": "dev@example.com"})
    return {"Authorization": "Bearer fake"}


@pytest.fixture
def linked_client(http, supabase_auth):
    """A client provisioned through the self-service flow and linked to SUPABASE_USER_ID -
    developer keys can only be minted for a client the caller's Supabase identity owns."""
    response = http.post("/clients/me", headers=supabase_auth)
    assert response.status_code == 200, response.text
    return response.json()


def test_create_developer_key_returns_raw_key_once(http, supabase_auth, linked_client):
    response = http.post("/clients/me/developer-keys", headers=supabase_auth, json={
        "name": "Claude Code", "scopes": ["sources:read", "sources:write"],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"].strip()
    assert body["scopes"] == ["sources:read", "sources:write"]

    listing = http.get("/clients/me/developer-keys", headers=supabase_auth).json()
    assert len(listing) == 1
    assert "key" not in listing[0]  # raw value is never returned again


def test_unknown_scope_is_rejected(http, supabase_auth, linked_client):
    response = http.post("/clients/me/developer-keys", headers=supabase_auth, json={
        "name": "bad", "scopes": ["not_a_real_scope"],
    })
    assert response.status_code == 422


def test_revoked_key_stops_authenticating(http, supabase_auth, linked_client):
    created = http.post("/clients/me/developer-keys", headers=supabase_auth, json={
        "name": "temp", "scopes": ["sources:read"],
    }).json()
    dev_headers = {"X-API-Key": created["key"]}

    ok = http.get("/data-sources", headers=dev_headers)
    assert ok.status_code == 200

    revoke = http.delete(f"/clients/me/developer-keys/{created['id']}", headers=supabase_auth)
    assert revoke.status_code == 200

    after = http.get("/data-sources", headers=dev_headers)
    assert after.status_code == 401


def test_scope_is_enforced_per_endpoint(http, supabase_auth, linked_client):
    read_only = http.post("/clients/me/developer-keys", headers=supabase_auth, json={
        "name": "read-only", "scopes": ["sources:read"],
    }).json()
    dev_headers = {"X-API-Key": read_only["key"]}

    listing = http.get("/data-sources", headers=dev_headers)
    assert listing.status_code == 200

    create = http.post("/data-sources", headers=dev_headers, json={
        "name": "x", "type": "csv_url", "product_type": "shop", "config": {"url": "https://example.invalid/x.csv", "format": "csv"},
    })
    assert create.status_code == 403


def test_secret_key_still_has_full_access_regardless_of_developer_keys(http, tenant):
    """Regression: minting/using developer keys must not change what the pre-existing
    secret key can do."""
    response = http.post("/data-sources", headers=tenant.secret, json={
        "name": "x", "type": "csv_url", "product_type": "shop", "config": {"url": "https://example.invalid/x.csv", "format": "csv"},
    })
    assert response.status_code == 201


def test_public_key_can_never_manage_data_sources(http, tenant):
    """Regression: the existing public key must stay confined to recommendations/events -
    it must never satisfy any /data-sources* scope, developer keys or not."""
    response = http.get("/data-sources", headers=tenant.public)
    assert response.status_code == 403
