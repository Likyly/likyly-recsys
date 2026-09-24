"""Items: string ids, upsert/get/list/delete, batch."""
import pytest

import db
from conftest import Q, SHOES, interactions_of, make_tenant

GID = "gid://shopify/Product/123456"


class TestUpsertGetDelete:
    def test_upsert_creates_then_replaces(self, http, tenant):
        first = http.put("/items/SKU-1", params=Q, headers=tenant.secret, json={"title": "A", "description": "d", "properties": {"category": "x", "price": 9.5}})
        assert first.status_code == 201
        assert first.json() == {"item_id": "SKU-1", "title": "A", "description": "d", "properties": {"category": "x", "price": 9.5}}

        again = http.put("/items/SKU-1", params=Q, headers=tenant.secret, json={"title": "A2", "properties": {"color": "red"}})
        assert again.status_code == 200
        # PUT replaces the whole item: the old description and properties are gone
        assert again.json() == {"item_id": "SKU-1", "title": "A2", "description": None, "properties": {"color": "red"}}

    @pytest.mark.parametrize("item_id", [GID, "3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", "SKU NIKE/001 é", "42", "x" * 255])
    def test_client_ids_of_any_shape_round_trip(self, http, tenant, item_id):
        from urllib.parse import quote
        path = quote(item_id, safe=":/") if item_id == GID else quote(item_id, safe="")
        assert http.put(f"/items/{path}", params=Q, headers=tenant.secret, json={"title": "T"}).status_code == 201
        got = http.get(f"/items/{path}", params=Q, headers=tenant.secret)
        assert got.status_code == 200 and got.json()["item_id"] == item_id

    def test_get_and_missing(self, http, seeded):
        assert http.get(f"/items/{GID}", params=Q, headers=seeded.secret).json()["title"] == "Timberland Winter Boot"
        missing = http.get("/items/nope", params=Q, headers=seeded.secret)
        assert missing.status_code == 404 and "request_id" in missing.json()

    def test_delete(self, http, seeded):
        assert http.delete(f"/items/{GID}", params=Q, headers=seeded.secret).status_code == 200
        assert http.get(f"/items/{GID}", params=Q, headers=seeded.secret).status_code == 404
        assert http.delete(f"/items/{GID}", params=Q, headers=seeded.secret).status_code == 404

    def test_delete_keeps_recorded_events(self, http, seeded):
        http.post("/events/purchase", params=Q, headers=seeded.secret, json={"user_id": "u", "item_id": "SKU-NIKE-001"})
        http.delete("/items/SKU-NIKE-001", params=Q, headers=seeded.secret)
        assert len(interactions_of(seeded)) == 1

    def test_title_is_required(self, http, tenant):
        assert http.put("/items/x", params=Q, headers=tenant.secret, json={"description": "no title"}).status_code == 422

    def test_catalogs_are_separate_namespaces(self, http, tenant):
        http.put("/items/a", params={"data_product_type": "one"}, headers=tenant.secret, json={"title": "in one"})
        assert http.get("/items/a", params={"data_product_type": "two"}, headers=tenant.secret).status_code == 404


class TestList:
    def test_list_paginates_and_reports_total(self, http, seeded):
        page = http.get("/items", params={**Q, "limit": 3, "offset": 0}, headers=seeded.secret)
        assert page.status_code == 200 and len(page.json()) == 3
        assert page.headers["x-total-count"] == str(len(SHOES))
        rest = http.get("/items", params={**Q, "limit": 100, "offset": 3}, headers=seeded.secret).json()
        assert len(rest) == len(SHOES) - 3
        assert {i["item_id"] for i in page.json()} | {i["item_id"] for i in rest} == {s[0] for s in SHOES}

    def test_list_limit_bounds(self, http, seeded):
        assert http.get("/items", params={**Q, "limit": 0}, headers=seeded.secret).status_code == 422
        assert http.get("/items", params={**Q, "limit": 5000}, headers=seeded.secret).status_code == 422


class TestGenericProperties:
    def test_any_domain_fits_in_properties(self, http, tenant):
        job = {"title": "Senior Rust engineer", "description": "Build a search backend", "properties": {"company": "Acme", "remote": True, "salary": {"min": 90000, "max": 120000}, "tags": ["rust", "search"]}}
        assert http.put("/items/job-77", params=Q, headers=tenant.secret, json=job).status_code == 201
        assert http.get("/items/job-77", params=Q, headers=tenant.secret).json()["properties"] == job["properties"]

    def test_engine_fields_are_derived_from_properties(self, http, tenant):
        http.put("/items/a", params=Q, headers=tenant.secret, json={"title": "A", "properties": {"category": "shoes", "price": "12.5", "year": 2020, "author": "Ann", "url": "http://x"}})
        internal = db.resolve_internal_ids(tenant.client_id, Q["data_product_type"], db.KIND_ITEM, ["a"])["a"]
        row = db.fetch_products(Q["data_product_type"], work_id=internal, client_id=tenant.client_id).iloc[0]
        assert (row["genre_1"], row["price"], row["year"], row["author"], row["url"]) == ("shoes", 12.5, 2020, "Ann", "http://x")

    def test_unusable_engine_values_do_not_fail_the_upsert(self, http, tenant):
        response = http.put("/items/a", params=Q, headers=tenant.secret, json={"title": "A", "properties": {"year": "n/a", "price": "free"}})
        assert response.status_code == 201
        assert response.json()["properties"] == {"year": "n/a", "price": "free"}

    def test_deprecated_top_level_fields_are_folded_into_properties(self, http, tenant):
        response = http.put("/items/a", params=Q, headers=tenant.secret, json={"title": "A", "genre_1": "drama", "price": 5, "properties": {"price": 7}})
        # properties wins over the deprecated field for the same key
        assert response.json()["properties"] == {"price": 7, "category": "drama"}

    def test_item_without_description_still_gets_content_recommendations(self, http, tenant):
        """Regression: description-less items used to be dropped from the similarity matrix,
        shifting every later row onto the wrong item."""
        for item_id, title in [("a", "Red running shoe"), ("b", "Blue running shoe"), ("c", "Green running shoe")]:
            http.put(f"/items/{item_id}", params=Q, headers=tenant.secret, json={"title": title, "properties": {"category": "shoes"}})
        response = http.post("/getRec", params=Q, headers=tenant.secret, json={"item_id": "b", "count": 5})
        assert response.status_code == 200
        assert {i["item_id"] for i in response.json()["items"]} == {"a", "c"}


class TestBatch:
    def test_import_upserts_many(self, http, tenant):
        items = [{"item_id": f"SKU-{i}", "title": f"Item {i}", "properties": {"category": "c"}} for i in range(25)]
        response = http.post("/items/import", params=Q, headers=tenant.secret, json={"items": items})
        assert response.status_code == 200
        assert response.json() == {"received": 25, "succeeded": 25, "failed": 0, "errors": []}
        assert http.get("/items", params=Q, headers=tenant.secret).headers["x-total-count"] == "25"
        # idempotent
        assert http.post("/items/import", params=Q, headers=tenant.secret, json={"items": items}).json()["succeeded"] == 25
        assert http.get("/items", params=Q, headers=tenant.secret).headers["x-total-count"] == "25"

    def test_import_validation(self, http, tenant):
        assert http.post("/items/import", params=Q, headers=tenant.secret, json={"items": []}).status_code == 422
        assert http.post("/items/import", params=Q, headers=tenant.secret, json={"items": [{"item_id": "x"}]}).status_code == 422

    def test_batch_delete_reports_missing_ids(self, http, seeded):
        response = http.post("/items/delete", params=Q, headers=seeded.secret, json={"item_ids": ["SKU-NIKE-001", "SKU-VANS-004", "ghost"]})
        assert response.status_code == 200
        body = response.json()
        assert (body["received"], body["succeeded"], body["failed"]) == (3, 2, 1)
        assert body["errors"] == [{"index": 2, "id": "ghost", "message": "No such item"}]
        assert http.get("/items", params=Q, headers=seeded.secret).headers["x-total-count"] == str(len(SHOES) - 2)


class TestPlanLimit:
    def test_free_plan_caps_new_items_but_not_updates(self, http):
        tenant = make_tenant(plan=db.PLAN_FREE)
        limit = db.get_plan_limits(db.PLAN_FREE)["product_limit"]
        result = http.post("/items/import", params=Q, headers=tenant.secret, json={"items": [{"item_id": str(i), "title": "t"} for i in range(limit + 3)]}).json()
        assert (result["succeeded"], result["failed"]) == (limit, 3)
        assert "Free plan limit" in result["errors"][0]["message"]

        blocked = http.put("/items/brand-new", params=Q, headers=tenant.secret, json={"title": "t"})
        assert blocked.status_code == 403 and "Free plan limit" in blocked.json()["detail"]
        assert http.put("/items/0", params=Q, headers=tenant.secret, json={"title": "updated"}).status_code == 200
