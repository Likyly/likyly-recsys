from datetime import datetime, timezone

import httpx
import pytest
from conftest import make_async_client, make_client

import likyly
from likyly import Likyly, RequestOptions

REC = {"recommendation_id": "rec_1", "strategy": "popular", "items": []}
EVT = {"message": "ok", "event_id": None, "duplicate": False}


def test_requires_an_api_key():
    for bad in ("", "  ", None):
        with pytest.raises(likyly.ValidationError):
            Likyly(bad)  # type: ignore[arg-type]


def test_defaults_to_the_production_api_and_appends_a_user_agent():
    seen = []
    client = Likyly("k", user_agent="my-shop/1.4", http_client=httpx.Client(transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200, json=[]))[1])))
    client.items.list()
    assert str(seen[0].url).startswith("https://api.likyly.com/items")
    assert seen[0].headers["user-agent"].startswith("likyly-python/") and seen[0].headers["user-agent"].endswith(" my-shop/1.4")


class TestValidationBeforeSending:
    def test_event_needs_item_and_user_or_session(self):
        client, script, _ = make_client([{"body": EVT}])
        with pytest.raises(likyly.ValidationError):
            client.events.view(item_id="a")
        with pytest.raises(likyly.ValidationError):
            client.events.view(user_id="u", item_id="")
        assert script.calls == []

    def test_ids_are_strings_never_coerced(self):
        client, script, _ = make_client([{"body": EVT}])
        with pytest.raises(likyly.ValidationError):
            client.events.view(item_id=42, user_id="u")  # type: ignore[arg-type]
        with pytest.raises(likyly.ValidationError):
            client.items.get(7)  # type: ignore[arg-type]
        assert script.calls == []

    def test_event_types_are_open_but_url_safe(self):
        client, _, _ = make_client([{"body": EVT}])
        client.events.track("favorite", item_id="i", user_id="u")
        with pytest.raises(likyly.ValidationError):
            client.events.track("bad type!", item_id="i", user_id="u")

    def test_advanced_endpoints_refuse_ids_containing_a_slash(self):
        client, script, _ = make_client([{"body": REC}])
        with pytest.raises(likyly.ValidationError, match=r"recommendations\.get\(\)"):
            client.recommendations.similar(item_id="gid://shopify/Product/1")
        client.recommendations.get(item_id="gid://shopify/Product/1")
        assert len(script.calls) == 1

    def test_session_needs_exactly_one_of_list_or_user(self):
        client, _, _ = make_client([{"body": REC}])
        for kwargs in ({}, {"viewed_item_ids": ["a"], "user_id": "u"}, {"viewed_item_ids": []}):
            with pytest.raises(likyly.ValidationError):
                client.recommendations.session(**kwargs)

    def test_batches_must_not_be_empty(self):
        client, _, _ = make_client([{"body": EVT}])
        with pytest.raises(likyly.ValidationError):
            client.items.upsert_many([])
        with pytest.raises(likyly.ValidationError):
            client.events.track_many([])

    def test_datetime_is_serialized_to_iso_8601(self):
        import json
        client, script, _ = make_client([{"body": EVT}])
        client.events.view(user_id="u", item_id="i", occurred_at=datetime(2026, 9, 24, 10, 30, tzinfo=timezone.utc))
        assert json.loads(script.calls[0].content)["occurred_at"] == "2026-09-24T10:30:00+00:00"

    def test_properties_keys_are_never_renamed_at_any_depth(self):
        import json
        client, script, _ = make_client([{"body": EVT}])
        client.events.purchase(user_id="u", item_id="i", properties={"orderId": "O-1", "nested": {"snakeCase_and_camelCase": 1}})
        assert json.loads(script.calls[0].content)["properties"] == {"orderId": "O-1", "nested": {"snakeCase_and_camelCase": 1}}


class TestErrors:
    def test_hierarchy(self):
        client, _, _ = make_client([{"status": 401, "body": {"detail": "nope", "request_id": "req_x"}}], max_retries=0)
        with pytest.raises(likyly.AuthenticationError) as info:
            client.items.get("a")
        assert isinstance(info.value, likyly.ApiError) and isinstance(info.value, likyly.LikylyError) and isinstance(info.value, Exception)
        assert "req_x" in str(info.value)

    def test_422_lists_the_offending_fields(self):
        client, _, _ = make_client([{"status": 422, "body": {"detail": [{"loc": ["body", "title"], "msg": "Field required"}]}}], max_retries=0)
        with pytest.raises(likyly.ValidationError, match="Field required"):
            client.items.get("a")

    def test_non_json_error_body_still_becomes_an_api_error(self):
        client = Likyly("k", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(502, text="<html>Bad gateway</html>"))))
        with pytest.raises(likyly.ApiError) as info:
            client.items.get("a")
        assert info.value.status_code == 502

    def test_network_failure(self):
        client, _, _ = make_client([{"fail": httpx.ConnectError("boom")}], max_retries=0)
        with pytest.raises(likyly.NetworkError):
            client.items.get("a")

    def test_timeout(self):
        client, _, _ = make_client([{"fail": httpx.ReadTimeout("slow")}], max_retries=0)
        with pytest.raises(likyly.RequestTimeoutError):
            client.items.get("a")


class TestRetries:
    def test_429_is_retried_for_every_request_honoring_retry_after(self):
        client, script, sleeps = make_client([{"status": 429, "headers": {"retry-after": "3"}, "body": {"detail": "slow"}}, {"body": EVT}])
        assert client.events.view(user_id="u", item_id="i").duplicate is False  # no event_id: still safe - 429 never reached the app
        assert len(script.calls) == 2 and sleeps == [3.0]

    def test_a_retry_after_beyond_a_minute_is_not_waited_for(self):
        client, script, _ = make_client([{"status": 429, "headers": {"retry-after": "600"}, "body": {"detail": "x"}}])
        with pytest.raises(likyly.RateLimitError) as info:
            client.items.get("a")
        assert info.value.retry_after == 600 and len(script.calls) == 1

    def test_exponential_backoff_with_jitter_then_gives_up(self):
        client, script, sleeps = make_client([{"status": 503, "body": {"detail": "down"}}], max_retries=3)
        with pytest.raises(likyly.ApiError):
            client.items.get("a")
        assert len(script.calls) == 4 and sleeps == [0.5, 1.0, 2.0]

    @pytest.mark.parametrize("status", [502, 503, 504])
    def test_idempotent_calls_retry_on_gateway_errors(self, status):
        client, script, _ = make_client([{"status": status, "body": {"detail": "x"}}, {"body": {"item_id": "a", "title": "t"}}])
        client.items.upsert("a", title="t")
        assert len(script.calls) == 2

    @pytest.mark.parametrize("first", [{"status": 503, "body": {"detail": "x"}}, {"fail": httpx.ConnectError("reset")}, {"fail": httpx.ReadTimeout("slow")}])
    def test_an_event_without_event_id_is_never_retried_on_an_ambiguous_failure(self, first):
        client, script, _ = make_client([first, {"body": EVT}])
        with pytest.raises(likyly.LikylyError):
            client.events.purchase(user_id="u", item_id="i")
        assert len(script.calls) == 1

    def test_an_event_with_event_id_is_retried_the_replay_is_harmless(self):
        client, script, _ = make_client([{"status": 503, "body": {"detail": "x"}}, {"body": {**EVT, "event_id": "e1", "duplicate": True}}])
        assert client.events.purchase(event_id="e1", user_id="u", item_id="i").duplicate is True
        assert len(script.calls) == 2

    def test_track_many_is_retried_only_if_every_event_has_an_event_id(self):
        batch = {"received": 2, "accepted": 2, "duplicates": 0}
        client, script, _ = make_client([{"status": 503, "body": {"detail": "x"}}, {"body": batch}])
        with pytest.raises(likyly.ApiError):
            client.events.track_many([{"type": "view", "user_id": "u", "item_id": "1"}, {"type": "view", "user_id": "u", "item_id": "2", "event_id": "e"}])
        assert len(script.calls) == 1
        client, script, _ = make_client([{"status": 503, "body": {"detail": "x"}}, {"body": batch}])
        client.events.track_many([{"type": "view", "user_id": "u", "item_id": "1", "event_id": "a"}, {"type": "view", "user_id": "u", "item_id": "2", "event_id": "b"}])
        assert len(script.calls) == 2

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_client_errors_are_never_retried(self, status):
        client, script, _ = make_client([{"status": status, "body": {"detail": "x"}}, {"body": {}}])
        with pytest.raises(likyly.LikylyError):
            client.items.get("a")
        assert len(script.calls) == 1

    def test_per_call_max_retries_overrides_the_client(self):
        client, script, _ = make_client([{"status": 503, "body": {"detail": "x"}}], max_retries=5)
        with pytest.raises(likyly.ApiError):
            client.items.get("a", options=RequestOptions(max_retries=0))
        assert len(script.calls) == 1


def test_lists_report_total_and_only_send_what_was_asked():
    client, script, _ = make_client([{"body": [{"item_id": "a", "title": "A"}], "headers": {"x-total-count": "42"}}])
    page = client.items.list(limit=1, offset=10)
    assert (page.total, page.limit, page.offset) == (42, 1, 10)
    client.items.list()
    assert dict(script.calls[1].url.params) == {}


def test_import_is_an_alias_of_upsert_many():
    client, script, _ = make_client([{"body": {"received": 1, "succeeded": 1, "failed": 0}}])
    client.items.import_([{"item_id": "a", "title": "A"}])
    assert script.calls[0].url.path == "/items/import"


async def test_async_client_retries_and_closes():
    client, script, sleeps = make_async_client([{"status": 503, "body": {"detail": "x"}}, {"body": {"item_id": "a", "title": "t"}}])
    async with client:
        assert (await client.items.upsert("a", title="t")).item_id == "a"
    assert len(script.calls) == 2 and sleeps == [0.5]
