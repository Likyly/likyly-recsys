"""OAuth access tokens (Claude, ChatGPT through mcp.likyly.com): Supabase Auth issues them, the
MCP server forwards them as X-API-Key, and the admin routers accept them like a developer key
with scoped_auth.OAUTH_SCOPES, on the workspace picked on likyly.com's consent page.

No real IdP in tests (same technique as test_developer_keys.py): _decode_supabase_jwt is
replaced by a lookup of the fake tokens below."""
import uuid

import pytest

import app as app_module
import db

OAUTH_CLIENT = "9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d"


@pytest.fixture
def account(monkeypatch, http):
    uid = f"supabase-user-oauth-{uuid.uuid4().hex[:8]}"
    tokens = {
        "Bearer website-session": {"sub": uid, "email": "oauth@example.com"},
        "Bearer eyJoauth.payload.sig": {"sub": uid, "email": "oauth@example.com", "client_id": OAUTH_CLIENT},
        "Bearer eyJsession.payload.sig": {"sub": uid, "email": "oauth@example.com"},
    }

    def decode(authorization):
        if authorization not in tokens:
            raise app_module.HTTPException(status_code=401, detail="Invalid session token")
        return tokens[authorization]

    monkeypatch.setattr(app_module, "_decode_supabase_jwt", decode)
    session = {"Authorization": "Bearer website-session"}
    first = http.post("/clients/me", headers=session).json()
    return {"session": session, "first_id": first["client_id"], "uid": uid}


OAUTH = {"X-API-Key": "eyJoauth.payload.sig"}


def _create_placement(http, headers, slug):
    response = http.post("/placements", headers=headers, json={
        "slug": slug, "name": slug, "product_type": "shop", "context_type": "product_page", "limit": 4, "strategy": "auto",
        "signals": {"required": ["current_item_id"], "optional": ["session_id"]},
    })
    assert response.status_code == 201, response.text


def test_oauth_token_acts_on_the_primary_workspace_by_default(http, account):
    _create_placement(http, OAUTH, "oauth-default")
    secret_listing = [p["slug"] for p in http.get("/placements", headers=OAUTH).json()]
    assert secret_listing == ["oauth-default"]
    assert http.get("/data-sources", headers=OAUTH).status_code == 200


def test_website_session_is_not_an_oauth_token(http, account):
    response = http.get("/placements", headers={"X-API-Key": "eyJsession.payload.sig"})
    assert response.status_code == 401
    assert "OAuth" in response.json()["detail"]


def test_invalid_token_is_refused(http, account):
    assert http.get("/placements", headers={"X-API-Key": "eyJforged.payload.sig"}).status_code == 401


def test_oauth_token_never_reaches_the_runtime_surface(http, account):
    # Recommendations/events stay the public key's: a token is not a key there.
    response = http.post("/placements/anything/recommend", headers=OAUTH, json={})
    assert response.status_code == 401


def test_consent_page_picks_the_workspace(http, account):
    db.set_client_plan(account["first_id"], db.PLAN_PRO)
    second = http.post("/clients/me/workspaces", headers=account["session"]).json()["client_id"]

    chosen = http.put(f"/clients/me/oauth-grants/{OAUTH_CLIENT}", headers={**account["session"], "X-Workspace-Id": str(second)})
    assert chosen.status_code == 200, chosen.text
    _create_placement(http, OAUTH, "oauth-second")

    assert [p["slug"] for p in http.get("/placements", headers=OAUTH).json()] == ["oauth-second"]
    assert db.get_oauth_workspace_grant(account["uid"], OAUTH_CLIENT) == second

    # Choosing again moves the assistant back.
    assert http.put(f"/clients/me/oauth-grants/{OAUTH_CLIENT}", headers=account["session"]).status_code == 200
    assert http.get("/placements", headers=OAUTH).json() == []


def test_grant_to_a_workspace_of_another_account_is_refused(http, account):
    response = http.put(f"/clients/me/oauth-grants/{OAUTH_CLIENT}", headers={**account["session"], "X-Workspace-Id": "999999"})
    assert response.status_code == 403
    assert db.get_oauth_workspace_grant(account["uid"], OAUTH_CLIENT) is None


def test_invalid_oauth_client_id_is_refused(http, account):
    assert http.put("/clients/me/oauth-grants/bad%20id", headers=account["session"]).status_code == 422


def test_deleting_the_workspace_removes_its_grants(http, account):
    http.put(f"/clients/me/oauth-grants/{OAUTH_CLIENT}", headers=account["session"])
    assert db.get_oauth_workspace_grant(account["uid"], OAUTH_CLIENT) == account["first_id"]
    db.delete_client(account["first_id"])
    assert db.get_oauth_workspace_grant(account["uid"], OAUTH_CLIENT) is None


@pytest.mark.parametrize("method, path", [
    ("GET", "/clients/me/workspaces"),
    ("GET", "/clients/me/developer-keys"),
    ("POST", "/clients/me/developer-keys"),
    ("DELETE", "/clients/me/account"),
    ("PUT", f"/clients/me/oauth-grants/{OAUTH_CLIENT}"),
])
def test_oauth_token_never_opens_the_console(http, account, method, path):
    """An assistant's token sent straight to the API, bypassing mcp.likyly.com, as a session:
    keys, workspaces, the consent grant and account deletion stay closed."""
    response = http.request(method, path, headers={"Authorization": "Bearer eyJoauth.payload.sig"})
    assert response.status_code == 401, (path, response.text)
    assert "mcp.likyly.com" in response.json()["detail"]
    assert http.get("/clients/me/workspaces", headers=account["session"]).status_code == 200
