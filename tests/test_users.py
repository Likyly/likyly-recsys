"""Users: string ids, generic properties, upsert/get/list/delete, batch."""
from conftest import Q, interactions_of



def put_user(http, tenant, user_id, body):
    return http.put(f"/users/{user_id}", params=Q, headers=tenant.secret, json=body)


class TestUpsertGetDelete:
    def test_upsert_with_generic_properties(self, http, tenant):
        properties = {"country": "FR", "age": 34, "segment": "premium"}
        created = put_user(http, tenant, "user_123", {"properties": properties})
        assert created.status_code == 201
        assert created.json()["user_id"] == "user_123" and created.json()["properties"] == properties

        replaced = put_user(http, tenant, "user_123", {"properties": {"country": "DE"}})
        assert replaced.status_code == 200
        assert replaced.json()["properties"] == {"country": "DE"}  # PUT replaces the whole profile

    def test_ids_of_any_shape(self, http, tenant):
        for user_id in ("f47ac10b-58cc-4372-a567-0e02b2c3d479", "jane@example.com", "42"):
            assert put_user(http, tenant, user_id, {"properties": {}}).status_code == 201
            assert http.get(f"/users/{user_id}", params=Q, headers=tenant.secret).json()["user_id"] == user_id

    def test_get_unknown_is_404(self, http, tenant):
        assert http.get("/users/ghost", params=Q, headers=tenant.secret).status_code == 404

    def test_empty_body_is_a_valid_profile(self, http, tenant):
        assert put_user(http, tenant, "u", {}).status_code == 201

    def test_legacy_profile_fields_are_still_accepted_and_folded_in(self, http, tenant):
        user = put_user(http, tenant, "u", {"user_firstname": "Ada", "user_lastname": "Lovelace", "user_age": 36, "properties": {"segment": "vip"}}).json()
        assert user["properties"] == {"segment": "vip", "age": 36, "firstname": "Ada", "lastname": "Lovelace"}
        assert user["user_firstlastname"] == "Ada Lovelace" and user["user_age"] == 36

    def test_properties_can_feed_the_display_name(self, http, tenant):
        user = put_user(http, tenant, "u", {"properties": {"firstname": "Grace", "lastname": "Hopper"}}).json()
        assert user["user_firstlastname"] == "Grace Hopper"

    def test_delete_erases_profile_and_events(self, http, seeded):
        put_user(http, seeded, "user_1", {"properties": {"a": 1}})
        http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": "user_1", "item_id": "SKU-NIKE-001"})
        http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": "user_2", "item_id": "SKU-NIKE-001"})
        assert http.delete("/users/user_1", params=Q, headers=seeded.secret).status_code == 200
        assert http.get("/users/user_1", params=Q, headers=seeded.secret).status_code == 404
        assert len(interactions_of(seeded)) == 1  # user_2's event is untouched
        assert http.delete("/users/user_1", params=Q, headers=seeded.secret).status_code == 404

    def test_delete_works_for_a_user_known_only_through_events(self, http, seeded):
        http.post("/events/view", params=Q, headers=seeded.public, json={"user_id": "event-only", "item_id": "SKU-NIKE-001"})
        assert http.delete("/users/event-only", params=Q, headers=seeded.secret).status_code == 200
        assert interactions_of(seeded) == []


class TestList:
    def test_list_and_total(self, http, tenant):
        for i in range(5):
            put_user(http, tenant, f"user_{i}", {"properties": {"i": i}})
        response = http.get("/users", params={**Q, "limit": 2}, headers=tenant.secret)
        assert response.status_code == 200 and len(response.json()) == 2
        assert response.headers["x-total-count"] == "5"
        assert len(http.get("/users", params=Q, headers=tenant.secret).json()) == 5  # historical: no limit -> everything
        assert {u["user_id"] for u in http.get("/users", params={**Q, "limit": 10, "offset": 2}, headers=tenant.secret).json()} <= {f"user_{i}" for i in range(5)}

    def test_legacy_user_id_filter(self, http, tenant):
        put_user(http, tenant, "user_1", {"properties": {}})
        assert http.get("/users", params={**Q, "user_id": "user_1"}, headers=tenant.secret).json()[0]["user_id"] == "user_1"
        assert http.get("/users", params={**Q, "user_id": "nobody"}, headers=tenant.secret).status_code == 404


class TestBatch:
    def test_import(self, http, tenant):
        users = [{"user_id": f"u{i}", "properties": {"n": i}} for i in range(12)]
        response = http.post("/users/import", params=Q, headers=tenant.secret, json={"users": users})
        assert response.json() == {"received": 12, "succeeded": 12, "failed": 0, "errors": []}
        assert http.get("/users/u7", params=Q, headers=tenant.secret).json()["properties"] == {"n": 7}

    def test_import_validation(self, http, tenant):
        assert http.post("/users/import", params=Q, headers=tenant.secret, json={"users": []}).status_code == 422
        assert http.post("/users/import", params=Q, headers=tenant.secret, json={"users": [{"properties": {}}]}).status_code == 422
