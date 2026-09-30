"""Connector-level unit tests - pagination, incremental checkpoints, retry, and field
mapping, exercised directly against the connectors package (no HTTP API, no database)."""
import pytest
import requests

from connectors import ConnectorError, NotSupported
from connectors.field_mapping import apply_mapping, resolve_path, suggest_mapping
from connectors.rest_api import RestApiConnector


class FakeResponse:
    def __init__(self, payload, status_code=200, links=None):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.links = links or {}
        self.text = str(payload)

    def json(self):
        return self._payload


def test_resolve_path_handles_missing_keys_and_indices():
    record = {"variants": [{"price": "9.99"}]}
    assert resolve_path(record, "variants[0].price") == "9.99"
    assert resolve_path(record, "variants[5].price") is None
    assert resolve_path(record, "nope") is None
    assert resolve_path(record, None) is None


def test_apply_mapping_requires_external_id_and_title():
    with pytest.raises(ValueError):
        apply_mapping({"name": "x"}, {"external_id": "id", "title": "name"})


def test_suggest_mapping_picks_shopify_shaped_defaults():
    sample = [{"id": 1, "title": "Shirt", "body_html": "desc", "product_type": "apparel", "variants": [{"price": "10.00"}]}]
    mapping = suggest_mapping(sample)
    assert mapping["external_id"] == "id"
    assert mapping["title"] == "title"
    assert mapping["description"] == "body_html"
    assert mapping["category"] == "product_type"
    assert mapping["price"] == "variants[0].price"


def test_rest_api_page_pagination_stops_on_short_page(monkeypatch):
    pages = [
        [{"id": str(i)} for i in range(3)],   # page 1: full (page_size=3) -> keep going
        [{"id": "3"}],                          # page 2: short -> last page
    ]

    def fake_request(self, params):
        page_num = params.get("page", 1)
        return FakeResponse({"items": pages[page_num - 1]})

    monkeypatch.setattr(RestApiConnector, "_request", fake_request)
    connector = RestApiConnector(
        {"base_url": "https://x.invalid", "items_path": "items", "pagination": {"type": "page", "page_size": 3}},
        {},
    )
    seen = [record for page in connector.full_sync(None) for record in page.raw_records]
    assert [r["id"] for r in seen] == ["0", "1", "2", "3"]


def test_rest_api_cursor_pagination_follows_next_cursor(monkeypatch):
    pages = {
        None: {"items": [{"id": "a"}], "next_cursor": "c2"},
        "c2": {"items": [{"id": "b"}], "next_cursor": None},
    }

    def fake_request(self, params):
        return FakeResponse(pages[params.get("cursor")])

    monkeypatch.setattr(RestApiConnector, "_request", fake_request)
    connector = RestApiConnector(
        {"base_url": "https://x.invalid", "items_path": "items", "pagination": {"type": "cursor", "next_cursor_path": "next_cursor"}},
        {},
    )
    seen = [record for page in connector.full_sync(None) for record in page.raw_records]
    assert [r["id"] for r in seen] == ["a", "b"]


def test_incremental_sync_seeds_updated_since_from_checkpoint(monkeypatch):
    captured_params = []

    def fake_request(self, params):
        captured_params.append(dict(params))
        return FakeResponse({"items": []})

    monkeypatch.setattr(RestApiConnector, "_request", fake_request)
    connector = RestApiConnector(
        {"base_url": "https://x.invalid", "items_path": "items", "updated_since_param": "updated_since"},
        {},
    )
    list(connector.incremental_sync({"last_updated_since": "2024-01-01T00:00:00Z"}))
    assert captured_params[0]["updated_since"] == "2024-01-01T00:00:00Z"


def test_incremental_sync_not_supported_without_config():
    connector = RestApiConnector({"base_url": "https://x.invalid"}, {})
    with pytest.raises(NotSupported):
        list(connector.incremental_sync({}))


def test_bad_auth_raises_connector_error_not_a_crash(monkeypatch):
    def fake_request(self, params):
        return FakeResponse({}, status_code=401)

    monkeypatch.setattr(RestApiConnector, "_request", fake_request)
    connector = RestApiConnector({"base_url": "https://x.invalid"}, {})
    result = connector.test_connection()
    assert result.ok is False
    assert "reject" in result.message.lower() or "401" in result.message


def test_transient_network_error_is_retried_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    def flaky_get(url, params=None, headers=None, auth=None, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise requests.ConnectionError("boom")
        return FakeResponse({"items": [{"id": "ok"}]})

    monkeypatch.setattr("connectors.rest_api.requests.get", flaky_get)
    connector = RestApiConnector({"base_url": "https://x.invalid", "items_path": "items"}, {})
    result = connector.test_connection()
    assert result.ok is True
    assert attempts["n"] == 3


def test_exhausted_retries_surface_as_connector_error(monkeypatch):
    def always_fails(url, params=None, headers=None, auth=None, timeout=None):
        raise requests.ConnectionError("still down")

    monkeypatch.setattr("connectors.rest_api.requests.get", always_fails)
    connector = RestApiConnector({"base_url": "https://x.invalid", "items_path": "items"}, {})
    with pytest.raises(ConnectorError):
        connector._get({})
