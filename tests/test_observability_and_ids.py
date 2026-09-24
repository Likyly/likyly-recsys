"""request_id, structured logs, error bodies; id helpers."""
import json
import re

import pytest
from pydantic import ValidationError

from conftest import Q
from ids import MAX_ID_LENGTH, new_recommendation_id, new_ulid, normalize_id, numeric_alias
from observability import coerce_request_id


class TestRequestId:
    def test_every_response_has_a_request_id(self, http, seeded):
        response = http.post("/getRec", params=Q, headers=seeded.public, json={})
        assert re.match(r"^req_[0-9A-Z]{26}$", response.headers["x-request-id"])

    def test_a_sane_caller_supplied_id_is_kept(self, http, seeded):
        response = http.post("/getRec", params=Q, headers={**seeded.public, "X-Request-ID": "my-trace-12345"}, json={})
        assert response.headers["x-request-id"] == "my-trace-12345"

    @pytest.mark.parametrize("bad", ["x", "has spaces in it", "a" * 200, "inject\nnewline"])
    def test_an_unsafe_caller_supplied_id_is_replaced(self, bad):
        assert coerce_request_id(bad).startswith("req_")

    def test_error_bodies_carry_the_request_id(self, http, seeded):
        for response in (
            http.get("/items/ghost", params=Q, headers=seeded.public),                       # 404
            http.get("/items", params=Q),                                                    # 401
            http.put("/items/x", params=Q, headers=seeded.public, json={"title": "t"}),      # 403
            http.post("/getRec", params=Q, headers=seeded.public, json={"count": 0}),        # 422
        ):
            body = response.json()
            assert body["request_id"] == response.headers["x-request-id"] and "detail" in body

    def test_unhandled_errors_are_a_clean_500_with_a_request_id(self, api, seeded, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setattr(api, "recommend_auto", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        response = TestClient(api.app, raise_server_exceptions=False).post("/getRec", params=Q, headers=seeded.public, json={})
        assert response.status_code == 500
        body = response.json()
        assert body["request_id"] == response.headers["x-request-id"] and "boom" not in json.dumps(body)


class TestLogs:
    def test_recommendation_log_carries_the_recommendation_id_and_the_request_id(self, http, seeded, log_records):
        response = http.post("/getRec", params=Q, headers=seeded.public, json={"session_id": "s", "placement": "homepage"})
        (line,) = [r for r in log_records if r.get("event") == "recommendation"]
        assert line["recommendation_id"] == response.json()["recommendation_id"]
        assert line["request_id"] == response.headers["x-request-id"]
        assert line["strategy"] == "popular" and line["placement"] == "homepage" and line["client_id"] == seeded.client_id

    def test_logs_never_contain_keys_or_personal_data(self, http, seeded, log_records):
        http.put("/users/jane@example.com", params=Q, headers=seeded.secret, json={"properties": {"email": "jane@example.com", "ssn": "123-45-6789"}})
        http.post("/getRec", params=Q, headers=seeded.public, json={"user_id": "jane@example.com", "session_id": "sess-secret-value"})
        http.get("/items", params=Q, headers={"X-API-Key": "bad-key-value-xyz"})
        dump = json.dumps(log_records)
        for forbidden in (seeded.secret_key, seeded.public_key, "bad-key-value-xyz", "123-45-6789", "jane@example.com", "sess-secret-value"):
            assert forbidden not in dump

    def test_access_log_uses_the_route_template_not_the_raw_url(self, http, seeded, log_records):
        http.get("/items/some-private-sku", params=Q, headers=seeded.public)
        assert any(r.get("path") == "/items/{item_id}" for r in log_records)
        assert "some-private-sku" not in json.dumps(log_records)


class TestIds:
    def test_ulid_shape_uniqueness_and_order(self):
        ids = [new_ulid() for _ in range(2000)]
        assert all(re.match(r"^[0-9A-HJKMNP-TV-Z]{26}$", i) for i in ids) and len(set(ids)) == 2000
        assert new_recommendation_id().startswith("rec_")

    @pytest.mark.parametrize("value,expected", [("user_123", "user_123"), (42, "42"), ("42", "42"), ("gid://shopify/Product/1", "gid://shopify/Product/1"), (" padded ", " padded ")])
    def test_normalize_id(self, value, expected):
        assert normalize_id(value) == expected

    @pytest.mark.parametrize("value", ["", "   ", True, 1.5, None, [], {}, "x" * (MAX_ID_LENGTH + 1), "a\nb", "a\x00b"])
    def test_normalize_id_rejects(self, value):
        with pytest.raises(ValueError):
            normalize_id(value)

    @pytest.mark.parametrize("value,expected", [("42", 42), ("0", 0), ("007", None), ("SKU-1", None), ("-5", None), ("4.5", None), ("99999999999", None), ("٣", None)])
    def test_numeric_alias_only_for_canonical_integers(self, value, expected):
        assert numeric_alias(value) == expected

    def test_event_schema_validation(self):
        from schemas import Event
        with pytest.raises(ValidationError):
            Event(item_id="a")
        assert Event(item_id="a", session_id="s").user_id is None
        assert Event(work_id=5, user_id=1).item_id == "5"
