"""Self-service workspace routes: read without provisioning, rename, list indexes.

Same Supabase-JWT technique as test_developer_keys.py (no real IdP in tests).
"""
import re
import uuid

import pytest

import app as app_module
import db

SUPABASE_USER_ID = "supabase-user-workspace-1"


@pytest.fixture
def supabase_auth(monkeypatch):
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": SUPABASE_USER_ID, "email": "ws@example.com"})
    return {"Authorization": "Bearer fake"}


def test_get_workspace_never_provisions_and_never_returns_keys(http, supabase_auth):
    # No workspace yet: the read-only route must not create one (that would burn the one-time key reveal).
    assert http.get("/clients/me", headers=supabase_auth).status_code == 404

    created = http.post("/clients/me", headers=supabase_auth).json()
    assert created["secret_key"]
    # The technical name is "li_" + 16 hex chars; no display name until the owner chooses one.
    assert re.fullmatch(r"li_[0-9a-f]{16}", created["name"]), created["name"]
    assert created["display_name"] is None

    body = http.get("/clients/me", headers=supabase_auth).json()
    assert body["client_id"] == created["client_id"]
    assert body["secret_key"] is None and body["public_key"] is None
    assert body["product_types"] == []


def test_rename_sets_the_display_name_and_keeps_the_technical_name(http, supabase_auth):
    technical_name = http.post("/clients/me", headers=supabase_auth).json()["name"]

    response = http.patch("/clients/me", headers=supabase_auth, json={"display_name": "  Acme Store  "})
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Acme Store"
    assert response.json()["name"] == technical_name

    body = http.get("/clients/me", headers=supabase_auth).json()
    assert (body["name"], body["display_name"]) == (technical_name, "Acme Store")

    # Renaming again replaces the display name only.
    again = http.patch("/clients/me", headers=supabase_auth, json={"display_name": "Acme Shoes"}).json()
    assert (again["name"], again["display_name"]) == (technical_name, "Acme Shoes")


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_rename_workspace_rejects_blank_or_too_long_names(http, supabase_auth, name):
    http.post("/clients/me", headers=supabase_auth)
    assert http.patch("/clients/me", headers=supabase_auth, json={"display_name": name}).status_code == 422


def test_the_old_rename_field_no_longer_renames(http, supabase_auth):
    # `name` is the technical identifier now; sending it is a malformed request, not a rename.
    http.post("/clients/me", headers=supabase_auth)
    assert http.patch("/clients/me", headers=supabase_auth, json={"name": "Acme"}).status_code == 422


def test_workspace_routes_require_a_workspace_and_a_session(http, monkeypatch):
    # An identity that never called POST /clients/me (the other tests share one database).
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": "supabase-user-without-workspace", "email": "none@example.com"})
    headers = {"Authorization": "Bearer fake"}
    assert http.patch("/clients/me", headers=headers, json={"display_name": "Nope"}).status_code == 404
    assert http.get("/clients/me/indexes", headers=headers).status_code == 404


def test_workspace_routes_require_a_session(http):
    # No monkeypatch here: the real JWT check rejects a missing Authorization header.
    assert http.get("/clients/me").status_code == 401
    assert http.get("/clients/me/indexes").status_code == 401
    assert http.patch("/clients/me", json={"display_name": "Nope"}).status_code == 401


def test_indexes_list_is_empty_for_a_new_workspace(http, supabase_auth):
    http.post("/clients/me", headers=supabase_auth)
    assert http.get("/clients/me/indexes", headers=supabase_auth).json() == []


class TestMultiWorkspace:
    # A fresh supabase_user_id per test, same reasoning as TestIndexItems below: a clean
    # account with no workspace yet, isolated from every other test in this module.
    @pytest.fixture
    def account(self, monkeypatch, http):
        uid = f"supabase-user-multi-ws-{uuid.uuid4().hex[:8]}"
        monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": uid, "email": "multi@example.com"})
        auth = {"Authorization": "Bearer fake"}
        first = http.post("/clients/me", headers=auth).json()
        return {"auth": auth, "first_id": first["client_id"]}

    def test_free_plan_cannot_create_a_second_workspace(self, http, account):
        response = http.post("/clients/me/workspaces", headers=account["auth"])
        assert response.status_code == 403
        assert "plan limit" in response.json()["detail"].lower()
        workspaces = http.get("/clients/me/workspaces", headers=account["auth"]).json()
        assert [w["id"] for w in workspaces] == [account["first_id"]]

    def test_pro_plan_can_create_and_switch_between_workspaces(self, http, account):
        db.set_client_plan(account["first_id"], db.PLAN_PRO)

        created = http.post("/clients/me/workspaces", headers=account["auth"])
        assert created.status_code == 201, created.text
        second = created.json()
        assert second["client_id"] != account["first_id"]
        assert second["secret_key"] and second["public_key"]  # shown once, like the very first workspace

        workspaces = http.get("/clients/me/workspaces", headers=account["auth"]).json()
        assert [w["id"] for w in workspaces] == [account["first_id"], second["client_id"]]  # oldest first
        assert workspaces[1]["plan"] == db.PLAN_FREE  # a new workspace starts Free, independent of the one that unlocked it

        # No X-Workspace-Id: every call still defaults to the primary (first) workspace -
        # unchanged behavior for every dashboard call written before multi-workspace existed.
        http.patch("/clients/me", headers=account["auth"], json={"display_name": "Primary"})
        http.patch("/clients/me", headers={**account["auth"], "X-Workspace-Id": str(second["client_id"])}, json={"display_name": "Second"})

        default = http.get("/clients/me", headers=account["auth"]).json()
        assert (default["client_id"], default["display_name"]) == (account["first_id"], "Primary")

        selected = http.get("/clients/me", headers={**account["auth"], "X-Workspace-Id": str(second["client_id"])}).json()
        assert (selected["client_id"], selected["display_name"]) == (second["client_id"], "Second")

    def test_x_workspace_id_must_belong_to_the_caller(self, http, account):
        # A workspace owned by a wholly different account - created directly (not via HTTP,
        # which would require re-pointing the shared _decode_supabase_jwt mock and racing
        # against the `account` fixture's own identity for the rest of this test).
        other_id, _secret, _public = db.create_client_for_supabase_user("other-account", "supabase-user-someone-else")
        response = http.get("/clients/me", headers={**account["auth"], "X-Workspace-Id": str(other_id)})
        assert response.status_code == 403

    def test_pro_workspace_limit_is_enforced_once_reached(self, http, account):
        db.set_client_plan(account["first_id"], db.PLAN_PRO)
        limit = db.get_plan_limits(db.PLAN_PRO)["workspace_limit"]
        for _ in range(limit - 1):  # one already exists (account["first_id"])
            assert http.post("/clients/me/workspaces", headers=account["auth"]).status_code == 201
        over_limit = http.post("/clients/me/workspaces", headers=account["auth"])
        assert over_limit.status_code == 403
        assert "plan limit" in over_limit.json()["detail"].lower()


class TestIndexItems:
    # A fresh supabase_user_id per test (not the file's shared SUPABASE_USER_ID/supabase_auth):
    # POST /clients/me only ever returns a secret_key on the account's first call for a given
    # uid, and this class needs a real one every time to push items through the secret-key API.
    @pytest.fixture
    def workspace(self, monkeypatch, http):
        uid = f"supabase-user-index-items-{uuid.uuid4().hex[:8]}"
        monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": uid, "email": "items@example.com"})
        auth = {"Authorization": "Bearer fake"}
        created = http.post("/clients/me", headers=auth).json()
        return {"auth": auth, "secret": {"X-API-Key": created["secret_key"]}}

    def _push(self, http, secret, index_name, count):
        for i in range(count):
            http.put(f"/items/sku-{i}", params={"data_product_type": index_name}, headers=secret, json={"title": f"Item {i}"})

    def test_browsing_an_index_shows_the_items_actually_synced(self, http, workspace):
        self._push(http, workspace["secret"], "shoes", 3)

        response = http.get("/clients/me/indexes/shoes/items", headers=workspace["auth"])
        assert response.status_code == 200
        assert response.headers["x-total-count"] == "3"
        assert {i["item_id"] for i in response.json()} == {"sku-0", "sku-1", "sku-2"}

    def test_browsing_paginates(self, http, workspace):
        self._push(http, workspace["secret"], "shoes", 5)

        page = http.get("/clients/me/indexes/shoes/items", params={"limit": 2, "offset": 2}, headers=workspace["auth"])
        assert page.headers["x-total-count"] == "5"
        assert len(page.json()) == 2

    def test_an_empty_or_unknown_index_reads_as_no_items_not_an_error(self, http, workspace):
        response = http.get("/clients/me/indexes/does-not-exist/items", headers=workspace["auth"])
        assert response.status_code == 200
        assert response.json() == [] and response.headers["x-total-count"] == "0"

    def test_cannot_browse_another_workspaces_index(self, http, workspace, monkeypatch):
        self._push(http, workspace["secret"], "shoes", 2)

        monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": "supabase-user-workspace-other", "email": "other@example.com"})
        other_auth = {"Authorization": "Bearer fake"}
        http.post("/clients/me", headers=other_auth)
        response = http.get("/clients/me/indexes/shoes/items", headers=other_auth)
        assert response.status_code == 200
        assert response.json() == []

    def test_requires_a_session(self, http):
        assert http.get("/clients/me/indexes/shoes/items").status_code == 401

    def test_search_matches_title_or_description(self, http, workspace):
        secret = workspace["secret"]
        http.put("/items/a", params={"data_product_type": "shoes"}, headers=secret, json={"title": "Nike Air Zoom", "description": "Running shoe"})
        http.put("/items/b", params={"data_product_type": "shoes"}, headers=secret, json={"title": "Adidas Boston", "description": "Fast trainer"})
        http.put("/items/c", params={"data_product_type": "shoes"}, headers=secret, json={"title": "Puma Velocity", "description": "For running too"})

        by_title = http.get("/clients/me/indexes/shoes/items", params={"search": "nike"}, headers=workspace["auth"])
        assert {i["item_id"] for i in by_title.json()} == {"a"} and by_title.headers["x-total-count"] == "1"

        by_description = http.get("/clients/me/indexes/shoes/items", params={"search": "running"}, headers=workspace["auth"])
        assert {i["item_id"] for i in by_description.json()} == {"a", "c"} and by_description.headers["x-total-count"] == "2"

        no_match = http.get("/clients/me/indexes/shoes/items", params={"search": "does-not-exist"}, headers=workspace["auth"])
        assert no_match.json() == [] and no_match.headers["x-total-count"] == "0"

    def test_sort_by_title_and_price_both_directions(self, http, workspace):
        secret = workspace["secret"]
        http.put("/items/a", params={"data_product_type": "shoes"}, headers=secret, json={"title": "Charlie", "properties": {"price": 30}})
        http.put("/items/b", params={"data_product_type": "shoes"}, headers=secret, json={"title": "Alpha", "properties": {"price": 10}})
        http.put("/items/c", params={"data_product_type": "shoes"}, headers=secret, json={"title": "Bravo"})  # no price

        def order(sort):
            return [i["item_id"] for i in http.get("/clients/me/indexes/shoes/items", params={"sort": sort}, headers=workspace["auth"]).json()]

        assert order("title") == ["b", "c", "a"]  # Alpha, Bravo, Charlie
        assert order("-title") == ["a", "c", "b"]
        assert order("price")[:2] == ["b", "a"] and order("price")[2] == "c"  # priced ascending, unpriced last either way
        assert order("-price")[:2] == ["a", "b"] and order("-price")[2] == "c"

    def test_unrecognized_sort_falls_back_to_the_default_rather_than_erroring(self, http, workspace):
        response = http.get("/clients/me/indexes/shoes/items", params={"sort": "not-a-real-field"}, headers=workspace["auth"])
        assert response.status_code == 422  # rejected by the query param's own enum, not silently ignored

    def test_edit_an_item(self, http, workspace):
        self._push(http, workspace["secret"], "shoes", 1)
        response = http.put(
            "/clients/me/indexes/shoes/items/sku-0", headers=workspace["auth"],
            json={"title": "Renamed", "description": "Updated via the dashboard"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["title"] == "Renamed"

        items = http.get("/clients/me/indexes/shoes/items", headers=workspace["auth"]).json()
        assert items[0]["title"] == "Renamed" and items[0]["description"] == "Updated via the dashboard"

    def test_edit_a_new_item_id_creates_it_and_returns_201(self, http, workspace):
        response = http.put("/clients/me/indexes/shoes/items/brand-new", headers=workspace["auth"], json={"title": "New one"})
        assert response.status_code == 201, response.text
        assert http.get("/clients/me/indexes/shoes/items", headers=workspace["auth"]).headers["x-total-count"] == "1"

    def test_edit_respects_the_free_plans_product_cap(self, http, workspace, monkeypatch):
        monkeypatch.setitem(db.PLAN_LIMITS[db.PLAN_FREE], "product_limit", 1)
        http.put("/clients/me/indexes/shoes/items/a", headers=workspace["auth"], json={"title": "First"})
        blocked = http.put("/clients/me/indexes/shoes/items/b", headers=workspace["auth"], json={"title": "Second"})
        assert blocked.status_code == 403 and "plan limit" in blocked.json()["detail"].lower()

    def test_delete_one_item(self, http, workspace):
        self._push(http, workspace["secret"], "shoes", 2)
        response = http.delete("/clients/me/indexes/shoes/items/sku-0", headers=workspace["auth"])
        assert response.status_code == 200

        remaining = http.get("/clients/me/indexes/shoes/items", headers=workspace["auth"])
        assert {i["item_id"] for i in remaining.json()} == {"sku-1"} and remaining.headers["x-total-count"] == "1"

    def test_delete_an_unknown_item_is_404(self, http, workspace):
        self._push(http, workspace["secret"], "shoes", 1)
        assert http.delete("/clients/me/indexes/shoes/items/does-not-exist", headers=workspace["auth"]).status_code == 404

    def test_delete_the_whole_index(self, http, workspace):
        self._push(http, workspace["secret"], "shoes", 3)
        response = http.delete("/clients/me/indexes/shoes", headers=workspace["auth"])
        assert response.status_code == 200 and "3" in response.json()["message"]

        assert http.get("/clients/me/indexes", headers=workspace["auth"]).json() == []
        assert http.get("/clients/me/indexes/shoes/items", headers=workspace["auth"]).headers["x-total-count"] == "0"

    def test_deleting_an_index_frees_the_free_plans_slot_for_a_new_one(self, http, workspace):
        self._push(http, workspace["secret"], "shoes", 1)  # opens the free plan's one-index slot
        blocked = http.put("/items/x", params={"data_product_type": "books"}, headers=workspace["secret"], json={"title": "t"})
        assert blocked.status_code == 403 and "1 catalog max" in blocked.json()["detail"]

        http.delete("/clients/me/indexes/shoes", headers=workspace["auth"])

        opened = http.put("/items/x", params={"data_product_type": "books"}, headers=workspace["secret"], json={"title": "t"})
        assert opened.status_code == 201, opened.text

    def test_deleting_an_unknown_index_is_404(self, http, workspace):
        assert http.delete("/clients/me/indexes/does-not-exist", headers=workspace["auth"]).status_code == 404

    def test_deleting_an_index_does_not_touch_its_data_source(self, http, workspace):
        created = http.post("/data-sources", headers=workspace["secret"], json={
            "name": "a", "type": "rest_api", "product_type": "shoes", "config": {"base_url": "https://example.invalid/p"},
        })
        assert created.status_code == 201, created.text
        self._push(http, workspace["secret"], "shoes", 1)

        http.delete("/clients/me/indexes/shoes", headers=workspace["auth"])

        remaining = http.get("/data-sources", headers=workspace["secret"]).json()
        assert [s["id"] for s in remaining] == [created.json()["id"]]

    def test_write_routes_require_a_session(self, http):
        assert http.put("/clients/me/indexes/shoes/items/a", json={"title": "x"}).status_code == 401
        assert http.delete("/clients/me/indexes/shoes/items/a").status_code == 401
        assert http.delete("/clients/me/indexes/shoes").status_code == 401


class TestPlacements:
    # Placements are created through the admin (secret/developer-key) surface - only reading
    # them back is Supabase-session-gated (the dashboard's own view), so this needs both.
    @pytest.fixture
    def workspace(self, monkeypatch, http):
        uid = f"supabase-user-placements-{uuid.uuid4().hex[:8]}"
        monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": uid, "email": "placements@example.com"})
        auth = {"Authorization": "Bearer fake"}
        created = http.post("/clients/me", headers=auth).json()
        return {"auth": auth, "secret": {"X-API-Key": created["secret_key"]}}

    def _create(self, http, secret, **overrides):
        body = {
            "slug": "pdp-related", "name": "Related products", "context_type": "product_page",
            "product_type": "shop", "limit": 4, "strategy": "auto",
            "signals": {"required": ["current_item_id"], "optional": ["session_id"]},
            **overrides,
        }
        response = http.post("/placements", headers=secret, json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def test_lists_what_the_admin_surface_created(self, http, workspace):
        self._create(http, workspace["secret"])
        self._create(http, workspace["secret"], slug="catalog-trending", context_type="listing_page", signals={"required": [], "optional": ["user_id"]})

        response = http.get("/clients/me/placements", headers=workspace["auth"])
        assert response.status_code == 200
        assert {p["slug"] for p in response.json()} == {"pdp-related", "catalog-trending"}

    def test_empty_for_a_workspace_with_no_placements_yet(self, http, workspace):
        assert http.get("/clients/me/placements", headers=workspace["auth"]).json() == []

    def test_health_reports_the_same_checks_the_mcp_validator_uses(self, http, workspace):
        self._create(http, workspace["secret"])
        response = http.get("/clients/me/placements/pdp-related/health", headers=workspace["auth"])
        assert response.status_code == 200
        body = response.json()
        assert body["slug"] == "pdp-related"
        assert {c["name"] for c in body["checks"]} >= {"recommendations_requested", "current_item_supplied"}

    def test_health_for_an_unknown_slug_is_404(self, http, workspace):
        assert http.get("/clients/me/placements/does-not-exist/health", headers=workspace["auth"]).status_code == 404

    def test_cannot_list_another_workspaces_placements(self, http, workspace, monkeypatch):
        self._create(http, workspace["secret"])
        monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": "supabase-user-placements-other", "email": "other@example.com"})
        other_auth = {"Authorization": "Bearer fake"}
        http.post("/clients/me", headers=other_auth)
        assert http.get("/clients/me/placements", headers=other_auth).json() == []

    def test_requires_a_session(self, http):
        assert http.get("/clients/me/placements").status_code == 401
        assert http.get("/clients/me/placements/pdp-related/health").status_code == 401

    def test_update_can_disable_and_change_the_basics(self, http, workspace):
        self._create(http, workspace["secret"])

        disabled = http.patch("/clients/me/placements/pdp-related", headers=workspace["auth"], json={"enabled": False})
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["enabled"] is False

        renamed = http.patch(
            "/clients/me/placements/pdp-related", headers=workspace["auth"], json={"name": "Vous aimerez aussi", "limit": 6, "enabled": True}
        )
        assert renamed.status_code == 200, renamed.text
        assert (renamed.json()["name"], renamed.json()["limit"], renamed.json()["enabled"]) == ("Vous aimerez aussi", 6, True)

    def test_update_on_an_unknown_slug_is_404(self, http, workspace):
        response = http.patch("/clients/me/placements/does-not-exist", headers=workspace["auth"], json={"enabled": False})
        assert response.status_code == 404

    def test_cannot_update_another_workspaces_placement(self, http, workspace, monkeypatch):
        self._create(http, workspace["secret"])
        monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": "supabase-user-placements-writer", "email": "writer@example.com"})
        other_auth = {"Authorization": "Bearer fake"}
        http.post("/clients/me", headers=other_auth)
        response = http.patch("/clients/me/placements/pdp-related", headers=other_auth, json={"enabled": False})
        assert response.status_code == 404  # scoped to the caller's own client_id, indistinguishable from "never existed"

    def test_tracking_requirements_mirror_matches_the_admin_route(self, http, workspace):
        self._create(http, workspace["secret"])
        http.patch("/clients/me/placements/pdp-related", headers=workspace["auth"], json={"tracking_configuration": {"event_types": ["reservation"]}})

        response = http.get("/clients/me/placements/pdp-related/tracking-requirements", headers=workspace["auth"])
        assert response.status_code == 200, response.text
        catalog_events = {e["event_type"] for e in response.json()["required_events"] if e["scope"] == "catalog"}
        assert catalog_events == {"reservation"}

    def test_write_routes_require_a_session(self, http):
        assert http.patch("/clients/me/placements/pdp-related", json={"enabled": False}).status_code == 401
        assert http.get("/clients/me/placements/pdp-related/tracking-requirements").status_code == 401
