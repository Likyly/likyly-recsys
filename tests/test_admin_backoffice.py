"""Admin back-office: which SDK / CMS / tool a workspace calls the API with, the platform overview,
one workspace in depth, the accounts list. Every route is admin-only (app_metadata.is_admin)."""
import app as app_module
import admin_queries
from conftest import CATALOG


def _as_admin(monkeypatch, is_admin=True):
    claims = {"sub": "admin-user", "email": "admin@example.com", "aal": "aal2", "app_metadata": {"is_admin": is_admin}}
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: claims)
    return {"Authorization": "Bearer fake"}


# --- Reading the caller --------------------------------------------------------------------------

def test_parses_each_kind_of_caller():
    parse = admin_queries.parse_client_agent
    assert parse("likyly-typescript/1.4.0") == {
        "agent": "typescript", "version": "1.4.0", "platform": None, "user_agent": "likyly-typescript/1.4.0",
    }
    wp = parse("likyly-wordpress/1.2.0 WordPress/6.6.2")
    assert (wp["agent"], wp["version"], wp["platform"]) == ("wordpress", "1.2.0", "WordPress 6.6.2")
    laravel = parse("likyly-php/1.4.0 likyly-laravel")
    assert (laravel["agent"], laravel["version"], laravel["platform"]) == ("laravel", "1.4.0", "likyly-php")
    wizard = parse("likyly-wizard/0.8.0 framework/nextjs")
    assert (wizard["agent"], wizard["platform"]) == ("wizard", "nextjs")
    assert parse("likyly-go/1.0.0 (go1.23.1) my-shop/1.4")["agent"] == "go"


def test_ignores_callers_that_are_not_ours():
    for header in (None, "", "curl/8.4.0", "Mozilla/5.0 (Macintosh) Chrome/130", "likyly-api"):
        assert admin_queries.parse_client_agent(header) is None


def test_agent_kinds():
    assert admin_queries.agent_kind("wordpress") == "cms"
    assert admin_queries.agent_kind("wizard") == "tool"
    assert admin_queries.agent_kind("python") == "sdk"


# --- Recording -----------------------------------------------------------------------------------

def test_records_the_integration_behind_a_key(http, seeded):
    app_module._record_integration_for_key(
        seeded.secret_key, admin_queries.parse_client_agent("likyly-wordpress/1.2.0 WordPress/6.6.2"),
    )
    app_module._record_integration_for_key(
        seeded.public_key, admin_queries.parse_client_agent("likyly-wordpress/1.3.0 WordPress/6.7"),
    )
    rows = [r for r in admin_queries.list_integrations() if r["client_id"] == seeded.client_id]
    assert len(rows) == 1  # one row per (workspace, agent), updated in place
    assert (rows[0]["version"], rows[0]["platform"], rows[0]["kind"]) == ("1.3.0", "WordPress 6.7", "cms")


def test_unknown_key_records_nothing(http):
    before = len(admin_queries.list_integrations())
    app_module._record_integration_for_key("sk_not-a-real-key", admin_queries.parse_client_agent("likyly-python/1.0.0"))
    assert len(admin_queries.list_integrations()) == before


# --- Routes ----------------------------------------------------------------------------------------

def test_admin_routes_refuse_a_non_admin(http, monkeypatch, seeded):
    headers = _as_admin(monkeypatch, is_admin=False)
    for path in ("/admin/overview", f"/admin/clients/{seeded.client_id}/insights", "/admin/integrations",
                 "/admin/upgrade-requests", "/admin/users"):
        assert http.get(path, headers=headers).status_code == 403, path


def test_overview_counts_the_platform(http, monkeypatch, seeded):
    headers = _as_admin(monkeypatch)
    response = http.get("/admin/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workspaces"] >= 1
    assert body["items"] >= 8
    assert body["workspaces_with_catalog"] >= 1
    assert len(body["requests_by_day"]) == 30
    assert len(body["signups_by_week"]) == 12


def test_client_insights_show_catalog_and_integrations(http, monkeypatch, seeded):
    app_module._record_integration_for_key(seeded.secret_key, admin_queries.parse_client_agent("likyly-python/2.0.0"))
    headers = _as_admin(monkeypatch)
    response = http.get(f"/admin/clients/{seeded.client_id}/insights", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["catalogs"][0]["catalog"] == CATALOG
    assert body["catalogs"][0]["items"] == 8
    assert [i["agent"] for i in body["integrations"]] == ["python"]
    assert http.get("/admin/clients/999999/insights", headers=headers).status_code == 404


def test_client_list_carries_the_back_office_figures(http, monkeypatch, seeded):
    headers = _as_admin(monkeypatch)
    rows = http.get("/admin/clients", headers=headers).json()
    mine = next(r for r in rows if r["id"] == seeded.client_id)
    assert mine["item_count"] == 8
    assert mine["catalog_count"] == 1


def test_users_need_the_supabase_secret_key(http, monkeypatch):
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    assert http.get("/admin/users", headers=_as_admin(monkeypatch)).status_code == 503


def test_users_lists_accounts_with_their_workspaces(http, monkeypatch, seeded):
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")

    class Page:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"users": [{
                "id": "owner-1", "email": "owner@example.com", "created_at": "2026-11-01T10:00:00Z",
                "last_sign_in_at": None, "email_confirmed_at": "2026-11-01T10:05:00Z",
                "app_metadata": {"provider": "github", "providers": ["github", "email"], "is_admin": True},
                "user_metadata": {"locale": "fr"}, "factors": [{"status": "verified"}],
            }]}

    monkeypatch.setattr(app_module.httpx, "get", lambda *args, **kwargs: Page())
    users = http.get("/admin/users", headers=_as_admin(monkeypatch)).json()
    assert users == [{
        "id": "owner-1", "email": "owner@example.com", "created_at": "2026-11-01T10:00:00Z",
        "last_sign_in_at": None, "email_confirmed": True, "providers": ["github", "email"],
        "is_admin": True, "mfa_enabled": True, "locale": "fr", "workspaces": [],
    }]
