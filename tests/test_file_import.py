"""The dashboard's file import (POST /clients/me/import/*): CSV as before, and JSON as a list of
objects with the same keys. Also the plan comparison the upgrade page reads. Supabase-JWT gated,
so these use the same technique as test_workspace.py (no real IdP in tests)."""
import io
import json

import pytest

import app as app_module
import db

SUPABASE_USER_ID = "supabase-user-file-import-1"


@pytest.fixture
def me(monkeypatch, http):
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": SUPABASE_USER_ID, "email": "import@example.com"})
    headers = {"Authorization": "Bearer fake"}
    created = http.post("/clients/me", headers=headers).json()
    db.set_client_plan(created["client_id"], db.PLAN_UNLIMITED)
    return {"headers": headers, "client_id": created["client_id"]}


def upload(http, me, route, filename, content, content_type="application/octet-stream", **params):
    return http.post(
        f"/clients/me/import/{route}", headers=me["headers"], params=params,
        files={"file": (filename, io.BytesIO(content if isinstance(content, bytes) else content.encode()), content_type)},
    )


PRODUCTS_CSV = "item_id,title,description,price\nSKU-1,Nike Air,Running shoe,129.9\nSKU-2,Adidas Boston,Fast shoe,159\n"
PRODUCTS = [
    {"item_id": "SKU-1", "title": "Nike Air", "description": "Running shoe", "price": 129.9},
    {"item_id": "SKU-2", "title": "Adidas Boston", "description": "Fast shoe", "price": 159},
]


def secret_key(me):
    return db.regenerate_secret_key(me["client_id"])


def test_csv_products_import_still_works(http, me):
    result = upload(http, me, "products", "catalogue.csv", PRODUCTS_CSV, "text/csv", data_product_type="csv-shop").json()
    assert (result["rows_total"], result["rows_ok"], result["errors"]) == (2, 2, [])
    indexes = {i["name"]: i["product_count"] for i in http.get("/clients/me/indexes", headers=me["headers"]).json()}
    assert indexes["csv-shop"] == 2


@pytest.mark.parametrize("filename,content_type", [("catalogue.json", "application/json"), ("export.JSON", "application/octet-stream"), ("data.txt", "application/json")])
def test_json_list_of_products(http, me, filename, content_type):
    result = upload(http, me, "products", filename, json.dumps(PRODUCTS), content_type, data_product_type="json-shop").json()
    assert (result["rows_total"], result["rows_ok"], result["errors"]) == (2, 2, [])

    key = secret_key(me)
    items = {i["item_id"]: i for i in http.get("/items", params={"data_product_type": "json-shop"}, headers={"X-API-Key": key}).json()}
    assert set(items) == {"SKU-1", "SKU-2"}
    assert items["SKU-1"]["title"] == "Nike Air" and items["SKU-1"]["properties"]["price"] == 129.9


def test_json_wrapped_in_an_object_and_numeric_ids_stay_text(http, me):
    payload = {"products": [{"item_id": 4007, "title": "Numeric id"}, {"item_id": "007", "title": "Leading zeros"}]}
    result = upload(http, me, "products", "wrapped.json", json.dumps(payload), data_product_type="wrapped-shop").json()
    assert result["rows_ok"] == 2
    ids = {i["item_id"] for i in http.get("/items", params={"data_product_type": "wrapped-shop"}, headers={"X-API-Key": secret_key(me)}).json()}
    assert ids == {"4007", "007"}


def test_json_row_errors_are_numbered_from_one(http, me):
    payload = [{"item_id": "ok", "title": "Fine"}, {"item_id": "no-title"}]
    result = upload(http, me, "products", "bad.json", json.dumps(payload), data_product_type="err-shop").json()
    assert (result["rows_total"], result["rows_ok"]) == (2, 1)
    assert result["errors"] == [{"row": 2, "message": "title is required"}]


def test_csv_row_errors_are_still_numbered_after_the_header(http, me):
    result = upload(http, me, "products", "bad.csv", "item_id,title\nok,Fine\nno-title,\n", "text/csv", data_product_type="err-csv").json()
    assert result["errors"] == [{"row": 3, "message": "title is required"}]


@pytest.mark.parametrize("content", ["not json at all", '"just a string"', '{"a": [1], "b": [2]}', "[1, 2, 3]", '{"a": 1}'])
def test_unusable_json_is_refused_with_a_clear_422(http, me, content):
    response = upload(http, me, "products", "broken.json", content, data_product_type="broken-shop")
    assert response.status_code == 422 and "Could not parse JSON" in response.json()["detail"]


def test_json_users_and_interactions(http, me):
    users = upload(http, me, "users", "users.json", json.dumps([{"user_id": "u1", "user_age": 34}, {"user_id": "u2"}]), data_product_type="json-events").json()
    assert (users["rows_total"], users["rows_ok"]) == (2, 2)

    events = [{"item_id": "SKU-1", "user_id": "u1", "quantity": 2}, {"item_id": "SKU-2", "session_id": "s-9"}]
    result = upload(http, me, "interactions", "events.json", json.dumps(events), data_product_type="json-events", event_type="purchase").json()
    assert (result["rows_total"], result["rows_ok"], result["errors"]) == (2, 2, [])


def test_the_free_plan_import_cannot_open_a_second_index(http, monkeypatch):
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": "supabase-user-file-import-free", "email": "free@example.com"})
    headers = {"Authorization": "Bearer fake"}
    http.post("/clients/me", headers=headers)  # a new workspace is on the free plan
    me = {"headers": headers}
    assert upload(http, me, "products", "a.json", json.dumps(PRODUCTS[:1]), data_product_type="first").json()["rows_ok"] == 1
    second = upload(http, me, "products", "b.json", json.dumps(PRODUCTS[:1]), data_product_type="second").json()
    assert second["rows_ok"] == 0 and "1 catalog max" in second["errors"][0]["message"]


def test_plan_comparison_lists_free_and_pro_but_not_the_internal_plan(http, me):
    plans = http.get("/clients/me/plans", headers=me["headers"]).json()
    assert [p["plan"] for p in plans] == ["free", "pro"]
    free, pro = plans
    assert (free["index_limit"], free["data_source_limit"], free["product_limit"], free["manual_sync_daily_limit"]) == (1, 1, 50, 1)
    assert pro["index_limit"] > free["index_limit"] and pro["product_limit"] > free["product_limit"]
    assert pro["manual_sync_daily_limit"] > free["manual_sync_daily_limit"]


def test_plan_comparison_needs_a_session(http):
    assert http.get("/clients/me/plans").status_code == 401
