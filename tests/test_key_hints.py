"""A non-secret preview of each API key ("sk_yVzt…gjP0") so the console can show something once a key
can no longer be revealed. Same Supabase-JWT technique as test_workspace.py."""
import uuid

import pytest

import app as app_module
import db

@pytest.fixture
def auth(monkeypatch):
    # A new identity per test: an existing workspace is never shown its keys again.
    user_id = f"supabase-user-key-hints-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": user_id, "email": "hints@example.com"})
    return {"Authorization": "Bearer fake"}


def hint_of(raw: str) -> str:
    return f"{raw[:7]}…{raw[-4:]}"


def test_new_workspace_keys_come_with_a_matching_preview(http, auth):
    created = http.post("/clients/me", headers=auth).json()
    assert created["secret_key_hint"] == hint_of(created["secret_key"])
    assert created["public_key_hint"] == hint_of(created["public_key"])
    assert created["secret_key_hint"].startswith("sk_") and created["public_key_hint"].startswith("pk_")


def test_the_preview_stays_available_after_the_key_is_no_longer_revealed(http, auth):
    created = http.post("/clients/me", headers=auth).json()
    later = http.get("/clients/me", headers=auth).json()
    assert later["secret_key"] is None and later["public_key"] is None
    assert (later["secret_key_hint"], later["public_key_hint"]) == (created["secret_key_hint"], created["public_key_hint"])


def test_the_preview_reveals_nothing_useful_about_the_key(http, auth):
    created = http.post("/clients/me", headers=auth).json()
    hint, raw = created["secret_key_hint"], created["secret_key"]
    assert len(hint) < len(raw) / 2 and raw not in hint
    assert raw[7:-4] not in hint  # the whole middle is withheld


def test_regenerating_a_key_replaces_only_its_own_preview(http, auth):
    created = http.post("/clients/me", headers=auth).json()

    secret = http.post("/clients/me/regenerate-secret-key", headers=auth).json()
    assert secret["secret_key_hint"] == hint_of(secret["secret_key"]) != created["secret_key_hint"]
    assert secret["public_key_hint"] == created["public_key_hint"]

    public = http.post("/clients/me/regenerate-public-key", headers=auth).json()
    assert public["public_key_hint"] == hint_of(public["public_key"]) != created["public_key_hint"]
    assert public["secret_key_hint"] == secret["secret_key_hint"]


def test_a_key_issued_before_previews_existed_has_none(http, auth):
    created = http.post("/clients/me", headers=auth).json()
    with db.SessionLocal() as session:
        session.query(db.ClientModel).filter_by(id=created["client_id"]).update({"secret_key_hint": None})
        session.commit()
    body = http.get("/clients/me", headers=auth).json()
    assert body["secret_key_hint"] is None and body["public_key_hint"] == created["public_key_hint"]


def test_revoking_a_key_drops_its_preview(http, auth):
    created = http.post("/clients/me", headers=auth).json()
    db.revoke_secret_key(created["client_id"])
    body = http.get("/clients/me", headers=auth).json()
    assert body["secret_key_hint"] is None and body["public_key_hint"] == created["public_key_hint"]


def test_developer_keys_are_listed_with_a_preview(http, auth):
    http.post("/clients/me", headers=auth)
    created = http.post("/clients/me/developer-keys", headers=auth, json={"name": "Claude Code", "scopes": ["sources:read"]}).json()
    assert created["key"].startswith("lk_") and created["key_hint"] == hint_of(created["key"])

    listed = {k["id"]: k for k in http.get("/clients/me/developer-keys", headers=auth).json()}
    assert listed[created["id"]]["key_hint"] == created["key_hint"]
    assert "key" not in listed[created["id"]]
