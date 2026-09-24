"""The OpenAPI document is a product surface (docs, SDK generation): check it says what the
backend actually does, in the new vocabulary."""
import json
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

ROOT = Path(__file__).resolve().parents[1]
HTTP_METHODS = {"get", "put", "post", "delete", "patch"}


@pytest.fixture(scope="module")
def spec(http):
    return http.get("/openapi.json").json()


def operations(spec):
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def test_committed_openapi_json_is_up_to_date(spec):
    committed = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    assert committed == spec, "docs/openapi.json is stale - run scripts/export_openapi.py"


def test_the_existing_api_was_modified_not_paralleled(spec):
    assert not any(path.startswith("/v1") for path in spec["paths"])
    assert {"/getRec", "/getRec/popular/{count}", "/events/{event_type}", "/items", "/users"} <= spec["paths"].keys()
    servers = [s["url"] for s in spec["servers"]]
    assert "/recsys-api" in servers and "https://api.likyly.com" in servers


def test_every_operation_has_summary_description_tag_and_documented_errors(api, spec):
    for route in api.app.routes:
        if isinstance(route, APIRoute) and route.include_in_schema:
            assert route.summary, f"{route.methods} {route.path} has no explicit summary"
    for path, method, operation in operations(spec):
        where = f"{method.upper()} {path}"
        assert operation.get("summary"), where
        assert (operation.get("description") or "").strip(), where
        assert operation.get("tags"), where
        assert operation.get("responses"), where
        if operation.get("x-required-key") or operation.get("security"):
            assert "401" in operation["responses"], f"{where}: 401 not documented"
        if operation.get("x-required-key") == "secret":
            assert "403" in operation["responses"], f"{where}: 403 not documented"
        if operation.get("parameters") or operation.get("requestBody"):
            assert "422" in operation["responses"], f"{where}: 422 not documented"


def test_operation_ids_are_unique_and_meaningful(spec):
    ids = [operation["operationId"] for _, _, operation in operations(spec)]
    assert len(ids) == len(set(ids))
    assert {"recommendations_get", "items_upsert", "items_list", "items_get", "items_delete", "items_import",
            "users_upsert", "users_get", "users_list", "users_delete", "users_import",
            "events_track", "events_batch"} <= set(ids)
    assert not any(re.search(r"_(get|post|put|delete)$", i) and "_" in i and i.count("_") > 4 for i in ids)


def test_security_is_declared_for_every_protected_operation(spec):
    assert spec["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-API-Key"
    assert spec["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    for path, method, operation in operations(spec):
        if path in ("/openapi.json", "/docs"):
            continue
        if path.startswith("/clients/me") or path.startswith("/admin"):
            assert operation["security"] == [{"bearerAuth": []}], f"{method} {path}"
        else:
            assert operation.get("security") == [{"APIKeyHeader": []}], f"{method} {path}"
            assert operation["x-required-key"] in ("secret", "public")


def test_deprecations_are_declared(spec):
    deprecated = {(m.upper(), p) for p, m, o in operations(spec) if o.get("deprecated")}
    # the deprecated routes are gone, not just flagged
    assert not [p for p in spec["paths"] if p.startswith(("/products", "/usersPurchases", "/usersRatings", "/usersPageViews"))]
    assert deprecated <= {("GET", "/getRec/contentVec/createIndex"), ("GET", "/getRec/contentVec/{product_id}/{count}")}
    for path in ("/items", "/items/{item_id}", "/getRec", "/events/{event_type}", "/users/{user_id}"):
        for method, operation in spec["paths"][path].items():
            assert not operation.get("deprecated"), f"{method} {path}"


def test_work_id_only_survives_as_a_deprecated_field(spec):
    for name, schema in spec["components"]["schemas"].items():
        for prop_name, prop in schema.get("properties", {}).items():
            if prop_name in ("work_id", "shared_work_ids", "source_work_ids"):
                assert prop.get("deprecated") is True, f"{name}.{prop_name} must be marked deprecated"
    for _path, _method, operation in operations(spec):
        for parameter in operation.get("parameters", []):
            if parameter["name"] == "viewed_work_ids":
                assert parameter.get("deprecated") is True
    # the canonical fields exist on every public schema that used to say work_id
    for name in ("RecommendedProduct",):
        assert "item_id" in spec["components"]["schemas"][name]["properties"]


def test_ids_are_strings_on_output_and_string_or_integer_on_input(spec):
    schemas = spec["components"]["schemas"]
    for name in ("Item", "RecommendedItem", "RecommendedProduct"):
        assert schemas[name]["properties"]["item_id"]["type"] == "string", name
    assert schemas["User"]["properties"]["user_id"]["type"] == "string"
    event = schemas["Event"]["properties"]
    for field in ("item_id", "user_id"):
        any_of = [option for option in event[field]["anyOf"] if option.get("type") != "null"]
        # [string-or-integer union, ...] flattened by the nullable wrapper
        assert any(o.get("type") == "string" for o in json.loads(json.dumps(any_of)) + [a for o in any_of for a in o.get("anyOf", [])]), field
        assert any(o.get("type") == "integer" and o.get("deprecated") for o in [a for o in any_of for a in o.get("anyOf", [])] + any_of), field


def test_public_json_is_snake_case(spec):
    for name, schema in spec["components"]["schemas"].items():
        for prop_name in schema.get("properties", {}):
            assert re.fullmatch(r"[a-z][a-z0-9_]*", prop_name), f"{name}.{prop_name}"
    for path, _method, operation in operations(spec):
        for parameter in operation.get("parameters", []):
            assert re.fullmatch(r"[a-z][a-z0-9_]*", parameter["name"]) or parameter["name"] == "X-API-Key", f"{path}: {parameter['name']}"


def test_request_and_response_schemas_carry_examples(spec):
    schemas = spec["components"]["schemas"]
    for name in ("Event", "EventBatch", "ItemImportRequest", "UserImportRequest", "RecommendationRequest",
                 "RecommendationResponse", "Item", "User", "EventResult", "EventBatchResult", "BatchResult"):
        assert schemas[name].get("examples"), f"{name} has no example"
    event_example = schemas["Event"]["examples"][0]
    assert {"user_id", "session_id", "item_id", "recommendation_id", "placement", "quantity", "occurred_at", "properties"} <= event_example.keys()
    assert "work_id" not in json.dumps([schemas[n]["examples"] for n in ("Event", "RecommendationRequest", "RecommendationResponse")])


def test_placement_is_a_free_string_never_an_enum_or_a_resource(spec):
    assert not any("placement" in path.lower() for path in spec["paths"])
    for name in ("Event", "RecommendationRequest", "RecommendationResponse"):
        placement = spec["components"]["schemas"][name]["properties"]["placement"]
        assert "enum" not in json.dumps(placement)
    shapes = {json.dumps(p.get("schema", {}).get("anyOf", p.get("schema"))) for _, _, o in operations(spec) for p in o.get("parameters", []) if p["name"] == "placement"}
    assert shapes and "enum" not in "".join(shapes)


def test_placement_is_accepted_by_every_recommendation_entry_point(spec):
    assert "placement" in spec["components"]["schemas"]["RecommendationRequest"]["properties"]
    for path in spec["paths"]:
        if path.startswith("/getRec/") and "contentVec" not in path:
            names = {p["name"] for p in spec["paths"][path]["get"]["parameters"]}
            assert {"placement", "session_id", "response_format"} <= names, path


def test_the_generic_event_route_remains_and_batch_is_documented(spec):
    assert "post" in spec["paths"]["/events/{event_type}"] and "post" in spec["paths"]["/events/batch"]
    assert not [p for p in spec["paths"] if p.startswith("/events/") and p not in ("/events/purchase", "/events/view", "/events/batch", "/events/{event_type}")]


def test_docs_page_and_schema_endpoints_are_served(http):
    assert http.get("/docs").status_code == 200
    assert http.get("/openapi.json").status_code == 200
