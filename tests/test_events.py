"""Event tracking: identified users, anonymous sessions, both, generic types, attribution, idempotency."""
import pytest

import db
from conftest import Q, interactions_of, make_tenant


def track(http, tenant, event_type, body, key="public"):
    return http.post(f"/events/{event_type}", params=Q, json=body, headers=getattr(tenant, key))


class TestActors:
    def test_identified_user(self, http, seeded):
        response = track(http, seeded, "view", {"user_id": "user_123", "item_id": "SKU-NIKE-001"})
        assert response.status_code == 200
        (row,) = interactions_of(seeded)
        assert row.user_id is not None and row.session_id is None and row.event_type == "view"

    def test_anonymous_session_without_user_id(self, http, seeded):
        response = track(http, seeded, "view", {"session_id": "sess_123", "item_id": "SKU-NIKE-001"})
        assert response.status_code == 200
        (row,) = interactions_of(seeded)
        assert row.user_id is None and row.session_id == "sess_123"

    def test_user_and_session_together(self, http, seeded):
        """Both ids on one event: what lets an anonymous history be attached to the user later."""
        track(http, seeded, "view", {"user_id": "user_789", "session_id": "sess_123", "item_id": "SKU-NIKE-001"})
        (row,) = interactions_of(seeded)
        assert row.user_id is not None and row.session_id == "sess_123"

    def test_neither_user_nor_session_is_rejected(self, http, seeded):
        response = track(http, seeded, "view", {"item_id": "SKU-NIKE-001"})
        assert response.status_code == 422
        assert "user_id or session_id" in response.text
        assert interactions_of(seeded) == []

    def test_item_id_is_required(self, http, seeded):
        response = track(http, seeded, "view", {"user_id": "user_1"})
        assert response.status_code == 422
        assert "item_id is required" in response.text

    def test_same_user_id_maps_to_one_internal_user(self, http, seeded):
        for item in ("SKU-NIKE-001", "SKU-VANS-004"):
            track(http, seeded, "view", {"user_id": "user_1", "item_id": item})
        first, second = interactions_of(seeded)
        assert first.user_id == second.user_id


class TestGenericEvents:
    @pytest.mark.parametrize("event_type", ["impression", "view", "click", "add_to_cart", "remove_from_cart", "purchase"])
    def test_officially_supported_types(self, http, seeded, event_type):
        assert track(http, seeded, event_type, {"session_id": "s", "item_id": "SKU-NIKE-001"}).status_code == 200
        assert [r.event_type for r in interactions_of(seeded)] == [event_type]

    def test_official_types_are_registered_for_every_new_tenant(self, tenant):
        registered = {e["event_type"] for e in db.get_client_event_types(tenant.client_id)}
        assert {"impression", "view", "click", "add_to_cart", "remove_from_cart", "purchase"} <= registered

    def test_unknown_event_type_is_auto_registered(self, http, seeded):
        assert "reservation" not in {e["event_type"] for e in db.get_client_event_types(seeded.client_id)}
        assert track(http, seeded, "reservation", {"user_id": "u", "item_id": "SKU-NIKE-001"}).status_code == 200
        registered = {e["event_type"]: e for e in db.get_client_event_types(seeded.client_id)}
        assert registered["reservation"]["tier"] == db.DEFAULT_EVENT_TIER

    def test_invalid_event_type_format_is_rejected(self, http, seeded):
        assert track(http, seeded, "bad type!", {"user_id": "u", "item_id": "x"}).status_code == 422

    def test_legacy_shortcuts_share_the_generic_behavior(self, http, seeded):
        assert http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": "s", "item_id": "SKU-NIKE-001"}).status_code == 200
        assert http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": "u", "item_id": "SKU-NIKE-001", "quantity": 2}).status_code == 200
        view, purchase = interactions_of(seeded)
        assert (view.event_type, view.quantity) == ("view", 1)
        assert (purchase.event_type, purchase.quantity) == ("purchase", 2)


class TestPayload:
    def test_properties_are_stored_verbatim_and_schema_free(self, http, seeded):
        properties = {"price": 129.90, "currency": "EUR", "order_id": "ORD-123", "revenue": 129.9, "custom": {"nested": [1, 2, {"a": None}]}}
        track(http, seeded, "purchase", {"user_id": "u", "item_id": "SKU-NIKE-001", "properties": properties})
        (row,) = interactions_of(seeded)
        assert row.properties == properties

    def test_oversized_properties_are_rejected(self, http, seeded):
        response = track(http, seeded, "view", {"user_id": "u", "item_id": "x", "properties": {"blob": "x" * 20_000}})
        assert response.status_code == 422

    def test_recommendation_id_and_placement_are_stored(self, http, seeded):
        track(http, seeded, "click", {"user_id": "u", "item_id": "SKU-NIKE-001", "recommendation_id": "rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF", "placement": "homepage"})
        (row,) = interactions_of(seeded)
        assert (row.recommendation_id, row.placement) == ("rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF", "homepage")

    def test_placement_is_a_free_string(self, http, seeded):
        for placement in ("homepage", "product_page", "my custom slot #3", "sidebar"):
            assert track(http, seeded, "impression", {"session_id": "s", "item_id": "SKU-NIKE-001", "placement": placement}).status_code == 200

    def test_occurred_at_defaults_to_server_time_and_accepts_explicit(self, http, seeded):
        track(http, seeded, "view", {"user_id": "u", "item_id": "SKU-NIKE-001"})
        track(http, seeded, "view", {"user_id": "u", "item_id": "SKU-NIKE-001", "occurred_at": "2026-09-24T10:30:00Z"})
        track(http, seeded, "view", {"user_id": "u", "item_id": "SKU-NIKE-001", "occurred_at": "2026-09-24T10:30:00"})
        now_row, explicit, naive = interactions_of(seeded)
        assert now_row.occurred_at is not None
        assert explicit.occurred_at.isoformat().startswith("2026-09-24T10:30:00")
        assert naive.occurred_at == explicit.occurred_at  # a time without a zone is read as UTC

    def test_events_for_items_not_in_the_catalog_are_accepted(self, http, seeded):
        """Collaborative filtering needs no catalog - events may reference any id."""
        assert track(http, seeded, "view", {"user_id": "u", "item_id": "never-uploaded"}).status_code == 200


class TestIdCompatibility:
    def test_integer_ids_are_accepted_and_become_strings(self, http, seeded):
        assert track(http, seeded, "view", {"user_id": 5, "item_id": 42}).status_code == 200
        with db.SessionLocal() as session:
            mapped = {(r.kind, r.external_id) for r in session.query(db.IdMapModel).filter_by(client_id=seeded.client_id)}
        assert {("user", "5"), ("item", "42")} <= mapped

    def test_work_id_is_a_deprecated_alias_of_item_id(self, http, seeded):
        assert track(http, seeded, "view", {"user_id": 1, "work_id": "SKU-NIKE-001"}).status_code == 200
        (row,) = interactions_of(seeded)
        with db.SessionLocal() as session:
            item = session.query(db.IdMapModel).filter_by(client_id=seeded.client_id, kind="item", external_id="SKU-NIKE-001").one()
        assert row.work_id == item.internal_id

    def test_conflicting_item_id_and_work_id_are_rejected(self, http, seeded):
        assert track(http, seeded, "view", {"user_id": 1, "item_id": "a", "work_id": "b"}).status_code == 422

    def test_boolean_and_float_ids_are_rejected(self, http, seeded):
        assert track(http, seeded, "view", {"user_id": True, "item_id": "a"}).status_code == 422
        assert track(http, seeded, "view", {"user_id": "u", "item_id": 1.5}).status_code == 422


class TestIdempotency:
    def test_same_event_id_records_once(self, http, seeded):
        body = {"user_id": "u", "item_id": "SKU-NIKE-001", "event_id": "evt_customer_1234", "properties": {"order_id": "ORD-1"}}
        first = track(http, seeded, "purchase", body).json()
        second = track(http, seeded, "purchase", body).json()
        assert first["duplicate"] is False and second["duplicate"] is True
        assert second["event_id"] == "evt_customer_1234"
        assert len(interactions_of(seeded)) == 1

    def test_events_without_event_id_are_never_deduplicated(self, http, seeded):
        for _ in range(3):
            track(http, seeded, "view", {"user_id": "u", "item_id": "SKU-NIKE-001"})
        assert len(interactions_of(seeded)) == 3

    def test_event_id_is_scoped_per_tenant(self, http, seeded):
        other = make_tenant()
        body = {"user_id": "u", "item_id": "x", "event_id": "evt_shared"}
        assert track(http, seeded, "purchase", body).json()["duplicate"] is False
        assert track(http, other, "purchase", body).json()["duplicate"] is False

    def test_retried_purchase_does_not_double_count_popularity(self, http, seeded):
        body = {"user_id": "u", "item_id": "SKU-NIKE-001", "event_id": "evt_1", "quantity": 1}
        for _ in range(5):
            track(http, seeded, "purchase", body)
        popularity = db.fetch_all_interactions(Q["data_product_type"], client_id=seeded.client_id)
        assert popularity["quantity"].sum() == 1


class TestBatch:
    def batch(self, http, tenant, events):
        return http.post("/events/batch", params=Q, json={"events": events}, headers=tenant.public)

    def test_records_every_event_with_its_own_type(self, http, seeded):
        response = self.batch(http, seeded, [
            {"event_type": "impression", "session_id": "s", "item_id": "SKU-NIKE-001", "recommendation_id": "rec_x", "placement": "homepage"},
            {"event_type": "click", "session_id": "s", "item_id": "SKU-NIKE-001", "recommendation_id": "rec_x", "placement": "homepage"},
            {"event_type": "purchase", "user_id": "u", "session_id": "s", "item_id": "SKU-NIKE-001", "properties": {"price": 1}},
        ])
        assert response.status_code == 200
        assert response.json() == {"received": 3, "accepted": 3, "duplicates": 0}
        assert [r.event_type for r in interactions_of(seeded)] == ["impression", "click", "purchase"]

    def test_duplicate_event_ids_are_skipped_including_within_one_batch(self, http, seeded):
        event = {"event_type": "purchase", "user_id": "u", "item_id": "SKU-NIKE-001", "event_id": "evt_dup"}
        response = self.batch(http, seeded, [event, event])
        assert response.json() == {"received": 2, "accepted": 1, "duplicates": 1}
        assert self.batch(http, seeded, [event]).json() == {"received": 1, "accepted": 0, "duplicates": 1}

    def test_one_malformed_event_rejects_the_batch_and_records_nothing(self, http, seeded):
        response = self.batch(http, seeded, [
            {"event_type": "view", "user_id": "u", "item_id": "SKU-NIKE-001"},
            {"event_type": "view", "item_id": "SKU-NIKE-001"},  # no user_id / session_id
        ])
        assert response.status_code == 422
        assert "events" in str(response.json()["detail"][0]["loc"]) and 1 in response.json()["detail"][0]["loc"]
        assert interactions_of(seeded) == []

    def test_limits(self, http, seeded):
        assert self.batch(http, seeded, []).status_code == 422
        too_many = [{"event_type": "view", "user_id": "u", "item_id": "x"}] * 1001
        assert self.batch(http, seeded, too_many).status_code == 422

    def test_batch_route_is_not_shadowed_by_the_generic_event_type_route(self, http, seeded):
        assert self.batch(http, seeded, [{"event_type": "view", "user_id": "u", "item_id": "x"}]).status_code == 200
        assert "batch" not in {e["event_type"] for e in db.get_client_event_types(seeded.client_id)}


class TestZeroWeightEvents:
    def test_impressions_do_not_count_as_popularity_or_training_signal(self, http, seeded):
        for _ in range(5):
            track(http, seeded, "impression", {"session_id": "s", "item_id": "SKU-NIKE-001"})
        import modelData
        assert modelData.compute_popularity_scores(Q["data_product_type"], client_id=seeded.client_id).empty
        assert modelData.build_user_item_matrix(Q["data_product_type"], client_id=seeded.client_id).nnz == 0

    def test_anonymous_events_feed_popularity_but_not_the_user_matrix(self, http, seeded):
        for _ in range(3):
            track(http, seeded, "view", {"session_id": "s", "item_id": "SKU-NIKE-001"})
        import modelData
        assert not modelData.compute_popularity_scores(Q["data_product_type"], client_id=seeded.client_id).empty
        assert modelData.build_user_item_matrix(Q["data_product_type"], client_id=seeded.client_id).nnz == 0

    def test_impressions_do_not_trigger_auto_retrain(self, seeded):
        for _ in range(10):
            db.insert_interaction(Q["data_product_type"], 1, None, "impression", client_id=seeded.client_id, session_id="s")
        assert db.count_interactions_since(seeded.client_id, Q["data_product_type"]) == 0


class TestAutoRetrain:
    def test_signal_events_trigger_it_and_impressions_do_not(self, http, seeded, api, monkeypatch):
        monkeypatch.setattr(api, "AUTO_RETRAIN_INTERACTION_THRESHOLD", 3)
        trained = []
        monkeypatch.setattr(api, "train_and_maybe_promote_model", lambda *a, **k: trained.append((a, k)) or {
            "version_id": 1, "precision_at_k": 0.5, "promoted": True, "previous_precision_at_k": None,
        })
        for _ in range(10):
            track(http, seeded, "impression", {"user_id": "u", "item_id": "SKU-NIKE-001"})
        assert trained == []
        for i in range(3):
            track(http, seeded, "purchase", {"user_id": f"u{i}", "item_id": "SKU-NIKE-001"})
        assert len(trained) >= 1
        assert not any(j["client_id"] == seeded.client_id and j["status"] == "failed" for j in api.GENERATE_MODEL_JOBS.values())
