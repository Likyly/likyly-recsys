"""Recommendations: automatic strategy selection, recommendation_id, placement, string ids,
attribution trace, legacy strategy endpoints."""
import random
import re

import pytest

import db
import modelData
import recommender
from conftest import Q, SHOES, interactions_of

REC_ID = re.compile(r"^rec_[0-9A-HJKMNP-TV-Z]{26}$")
RUNNING = {"SKU-NIKE-001", "SKU-ADIDAS-007", "SKU-ASICS-003", "SKU-SALOMON-010"}


def get_rec(http, tenant, body=None, key="public", **params):
    return http.post("/getRec", params={**Q, **params}, json=body or {}, headers=getattr(tenant, key))


def ids(response):
    return [item["item_id"] for item in response.json()["items"]]


def track(http, tenant, event_type, body):
    assert http.post(f"/events/{event_type}", params=Q, headers=tenant.public, json=body).status_code == 200


@pytest.fixture
def trained(http, seeded, tmp_path, monkeypatch):
    """`seeded` + purchases from 10 users in two taste clusters + a trained, promoted ALS model."""
    monkeypatch.setattr(modelData, "dvc_push", lambda *a, **k: None)
    monkeypatch.setattr(modelData, "_log_training_run_to_mlflow", lambda **k: None)
    monkeypatch.setattr(modelData, "_versioned_model_path", lambda t, c, v: str(tmp_path / f"{t}_{c}_v{v}.npz"))
    rng = random.Random(7)
    runners = ["SKU-NIKE-001", "SKU-ADIDAS-007", "SKU-ASICS-003", "SKU-SALOMON-010"]
    casual = ["gid://shopify/Product/123456", "3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", "SKU-CONVERSE-002", "SKU-VANS-004"]
    events = []
    for u in range(10):
        for item in rng.sample(runners if u < 5 else casual, 3):
            events.append({"event_type": "purchase", "user_id": f"user_{u}", "item_id": item})
    assert http.post("/events/batch", params=Q, headers=seeded.public, json={"events": events}).status_code == 200
    result = modelData.train_and_maybe_promote_model(Q["data_product_type"], client_id=seeded.client_id)
    assert result["promoted"]
    return seeded


class TestPlanStrategies:
    """The pure decision function - no database."""

    def plan(self, **overrides):
        signals = dict(has_user=False, has_item=False, has_viewed=False, has_history=False, user_has_signal=False, model_available=False)
        return recommender.plan_strategies(**{**signals, **overrides})

    def test_nothing_is_popular(self):
        assert self.plan() == ["popular"]

    def test_user_and_item_prefer_hybrid_then_degrade(self):
        assert self.plan(has_user=True, has_item=True) == ["hybrid", "content", "popular"]

    def test_item_only_is_content(self):
        assert self.plan(has_item=True) == ["content", "popular"]

    def test_viewed_items_are_session(self):
        assert self.plan(has_viewed=True) == ["session", "popular"]

    def test_user_with_history_and_model_is_collaborative(self):
        assert self.plan(has_user=True, user_has_signal=True, model_available=True)[0] == "collaborative"

    def test_user_without_model_falls_to_tracked_history_then_popular(self):
        assert self.plan(has_user=True, user_has_signal=True, has_history=True) == ["session_history", "popular"]
        assert self.plan(has_user=True, user_has_signal=True) == ["popular"]

    def test_user_without_events_gets_nothing_personal(self):
        assert self.plan(has_user=True, model_available=True) == ["popular"]

    def test_popular_is_always_the_last_resort(self):
        for flags in [dict(has_user=True, has_item=True, has_viewed=True, has_history=True, user_has_signal=True, model_available=True), dict()]:
            assert self.plan(**flags)[-1] == "popular"

    def test_precedence_item_beats_viewed_beats_collaborative(self):
        full = self.plan(has_user=True, has_item=True, has_viewed=True, has_history=True, user_has_signal=True, model_available=True)
        assert full == ["hybrid", "content", "session", "collaborative", "session_history", "popular"]

    def test_both_session_variants_are_public_strategy_session(self):
        assert recommender.public_strategy_name("session_history") == "session"
        assert recommender.public_strategy_name("hybrid") == "hybrid"


class TestRecommendationId:
    def test_always_present_and_unique_and_ulid_shaped(self, http, seeded):
        seen = set()
        for _ in range(5):
            response = get_rec(http, seeded)
            assert response.status_code == 200
            recommendation_id = response.json()["recommendation_id"]
            assert REC_ID.match(recommendation_id)
            assert response.headers["x-recommendation-id"] == recommendation_id
            seen.add(recommendation_id)
        assert len(seen) == 5

    def test_present_even_for_an_empty_catalog(self, http, tenant):
        response = get_rec(http, tenant)
        assert response.status_code == 200
        body = response.json()
        assert REC_ID.match(body["recommendation_id"]) and body["items"] == [] and body["strategy"] == "popular"

    def test_ids_sort_by_creation_time(self):
        import time
        from ids import new_recommendation_id
        first = new_recommendation_id()
        time.sleep(0.005)
        assert new_recommendation_id() > first


class TestStrategies:
    def test_empty_request_falls_back_to_popular(self, http, seeded):
        track(http, seeded, "purchase", {"user_id": "u", "item_id": "SKU-VANS-004", "quantity": 3})
        track(http, seeded, "view", {"user_id": "u", "item_id": "SKU-NIKE-001"})
        response = get_rec(http, seeded, {"count": 3})
        assert response.json()["strategy"] == "popular"
        assert ids(response)[0] == "SKU-VANS-004"  # the most purchased item leads
        assert len(ids(response)) == 3  # topped up from the catalog when few items have signal

    def test_popular_on_a_brand_new_catalog_returns_catalog_items(self, http, seeded):
        response = get_rec(http, seeded, {"count": 4})
        assert response.json()["strategy"] == "popular" and len(ids(response)) == 4

    def test_item_selects_content(self, http, seeded):
        response = get_rec(http, seeded, {"item_id": "SKU-NIKE-001", "count": 3})
        body = response.json()
        assert body["strategy"] == "content"
        assert "SKU-NIKE-001" not in ids(response)
        assert set(ids(response)) <= RUNNING | {"SKU-CONVERSE-002", "SKU-VANS-004"}
        assert ids(response)[0] in RUNNING
        assert body["items"][0]["explanation"]["reason"]

    def test_viewed_item_ids_select_session(self, http, seeded):
        response = get_rec(http, seeded, {"session_id": "sess_123", "viewed_item_ids": ["SKU-NIKE-001", "SKU-ADIDAS-007"], "count": 4})
        assert response.json()["strategy"] == "session"
        assert not ({"SKU-NIKE-001", "SKU-ADIDAS-007"} & set(ids(response)))
        assert ids(response)[0] in RUNNING

    def test_anonymous_session_uses_its_own_tracked_views(self, http, seeded):
        track(http, seeded, "view", {"session_id": "sess_boots", "item_id": "gid://shopify/Product/123456"})
        response = get_rec(http, seeded, {"session_id": "sess_boots", "count": 3})
        assert response.json()["strategy"] == "session"
        assert ids(response)[0] == "3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f"  # the other boot

    def test_unknown_session_and_user_degrade_to_popular_without_error(self, http, seeded):
        response = get_rec(http, seeded, {"user_id": "never-seen", "session_id": "never-seen", "count": 3})
        assert response.status_code == 200 and response.json()["strategy"] == "popular"

    def test_unknown_item_degrades_instead_of_failing(self, http, seeded):
        response = get_rec(http, seeded, {"item_id": "not-in-catalog", "count": 3})
        assert response.status_code == 200 and response.json()["strategy"] == "popular"

    def test_unknown_viewed_ids_are_ignored(self, http, seeded):
        response = get_rec(http, seeded, {"viewed_item_ids": ["ghost-1", "ghost-2"], "count": 3})
        assert response.status_code == 200 and response.json()["strategy"] == "popular"

    def test_user_with_tracked_views_but_no_model_uses_session_history(self, http, seeded):
        track(http, seeded, "view", {"user_id": "user_9", "item_id": "SKU-CONVERSE-002"})
        track(http, seeded, "purchase", {"user_id": "user_9", "item_id": "SKU-CONVERSE-002"})
        response = get_rec(http, seeded, {"user_id": "user_9", "count": 3})
        assert response.json()["strategy"] == "session" and ids(response)[0] == "SKU-VANS-004"

    def test_user_selects_collaborative_when_a_model_exists(self, http, trained):
        response = get_rec(http, trained, {"user_id": "user_0", "count": 3})
        body = response.json()
        assert body["strategy"] == "collaborative"
        assert set(ids(response)) <= {s[0] for s in SHOES}
        assert all(i["explanation"]["collaborative_score"] is not None for i in body["items"])

    def test_user_and_item_select_hybrid(self, http, trained):
        response = get_rec(http, trained, {"user_id": "user_0", "item_id": "SKU-NIKE-001", "count": 3})
        body = response.json()
        assert body["strategy"] == "hybrid"
        assert "SKU-NIKE-001" not in ids(response)
        assert "Hybride" in body["items"][0]["explanation"]["reason"]

    def test_hybrid_for_a_user_the_model_has_never_seen_degrades_to_content_ranking(self, http, trained):
        response = get_rec(http, trained, {"user_id": "brand-new-user", "item_id": "SKU-NIKE-001", "count": 3})
        # unknown user -> no user signal -> plain content
        assert response.status_code == 200 and response.json()["strategy"] == "content"

    def test_count_is_respected_and_bounded(self, http, seeded):
        assert len(ids(get_rec(http, seeded, {"count": 2}))) == 2
        assert get_rec(http, seeded, {"count": 0}).status_code == 422
        assert get_rec(http, seeded, {"count": 101}).status_code == 422

    def test_string_ids_round_trip_including_urls_and_uuids(self, http, seeded):
        response = get_rec(http, seeded, {"item_id": "gid://shopify/Product/123456", "count": 8})
        assert "3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f" in ids(response)
        assert all(isinstance(i["item_id"], str) for i in response.json()["items"])

    def test_integer_ids_are_accepted_in_the_request(self, http, tenant):
        for i in (1, 2, 3):
            http.put(f"/items/{i}", params=Q, headers=tenant.secret, json={"title": f"Film {i}", "description": "space adventure"})
        response = get_rec(http, tenant, {"item_id": 1, "viewed_item_ids": [2], "count": 3})
        assert response.status_code == 200 and set(ids(response)) <= {"1", "2", "3"}

    def test_alpha_is_not_exposed_on_the_automatic_endpoint(self, http, seeded):
        schema = http.get("/openapi.json").json()
        body = schema["components"]["schemas"]["RecommendationRequest"]["properties"]
        assert "alpha" not in body
        assert "alpha" in {p["name"] for p in schema["paths"]["/getRec/hybrid/{user_id}/{product_id}/{count}"]["get"]["parameters"]}

    def test_item_payload_shape(self, http, seeded):
        item = get_rec(http, seeded, {"item_id": "SKU-NIKE-001", "count": 1}).json()["items"][0]
        assert set(item) == {"item_id", "score", "title", "description", "properties", "explanation"}
        assert item["properties"]["brand"] and "work_id" not in item

    def test_similar_users_never_leak_through_the_public_response(self, http, trained):
        response = get_rec(http, trained, {"user_id": "user_0", "count": 3})
        assert all(i["explanation"].get("similar_users") is None for i in response.json()["items"])

    def test_debug_needs_the_secret_key(self, http, trained):
        assert get_rec(http, trained, {"user_id": "user_0", "debug": True}, key="public").status_code == 403
        response = get_rec(http, trained, {"user_id": "user_0", "debug": True}, key="secret")
        assert response.status_code == 200
        similar = [su for i in response.json()["items"] for su in (i["explanation"].get("similar_users") or [])]
        assert similar and all(isinstance(su["user_id"], str) and "name" not in su for su in similar)


class TestPlacementAndTrace:
    def test_placement_is_echoed_and_free_form(self, http, seeded):
        for placement in ("homepage", "related_products", "my own slot", None):
            body = get_rec(http, seeded, {"placement": placement, "count": 2}).json()
            assert body["placement"] == placement

    def test_every_recommendation_is_persisted_with_its_context(self, http, seeded):
        response = get_rec(http, seeded, {"user_id": "user_1", "session_id": "sess_abc", "item_id": "SKU-NIKE-001", "placement": "product_page", "count": 3})
        body = response.json()
        trace = db.get_recommendation(seeded.client_id, body["recommendation_id"])
        assert trace["user_id"] == "user_1" and trace["session_id"] == "sess_abc"
        assert trace["item_id"] == "SKU-NIKE-001" and trace["placement"] == "product_page"
        assert trace["strategy"] == body["strategy"] and trace["origin"] == "auto"
        assert trace["item_ids"] == [i["item_id"] for i in body["items"]]  # ids only, in rank order

    def test_events_carrying_the_recommendation_id_can_be_attributed(self, http, seeded):
        body = get_rec(http, seeded, {"session_id": "s", "placement": "homepage", "count": 2}).json()
        recommendation_id, shown = body["recommendation_id"], body["items"][0]["item_id"]
        track(http, seeded, "impression", {"session_id": "s", "item_id": shown, "recommendation_id": recommendation_id, "placement": "homepage"})
        track(http, seeded, "click", {"session_id": "s", "item_id": shown, "recommendation_id": recommendation_id, "placement": "homepage"})
        track(http, seeded, "purchase", {"session_id": "s", "item_id": shown, "recommendation_id": recommendation_id, "properties": {"revenue": 99}})
        attributed = interactions_of(seeded, recommendation_id=recommendation_id)
        assert [r.event_type for r in attributed] == ["impression", "click", "purchase"]
        assert db.get_recommendation(seeded.client_id, recommendation_id) is not None

    def test_a_failed_trace_write_never_fails_the_recommendation(self, http, seeded, monkeypatch, api):
        def boom(**_):
            raise RuntimeError("db down")
        monkeypatch.setattr(api, "store_recommendation", boom)
        response = get_rec(http, seeded)
        assert response.status_code == 200 and REC_ID.match(response.json()["recommendation_id"])


class TestLegacyStrategyEndpoints:
    def test_default_response_is_still_a_bare_array_with_the_id_in_a_header(self, http, seeded):
        response = http.get("/getRec/popular/3", params=Q, headers=seeded.public)
        assert response.status_code == 200 and isinstance(response.json(), list)
        assert REC_ID.match(response.headers["x-recommendation-id"])
        assert response.headers["x-recommendation-strategy"] == "popular"
        assert db.get_recommendation(seeded.client_id, response.headers["x-recommendation-id"])["origin"] == "explicit"

    def test_response_format_object_returns_the_common_envelope(self, http, seeded):
        response = http.get("/getRec/content/SKU-NIKE-001/3", params={**Q, "response_format": "object", "placement": "product_page"}, headers=seeded.public)
        body = response.json()
        assert set(body) == {"recommendation_id", "strategy", "placement", "items"}
        assert body["strategy"] == "content" and body["placement"] == "product_page"
        assert body["recommendation_id"] == response.headers["x-recommendation-id"]

    def test_array_items_keep_the_historical_fields_plus_item_id(self, http, tenant):
        for i, t, d in [(10, "Alien", "space marines hunt a xenomorph"), (11, "Aliens", "space marines hunt xenomorphs"), (12, "Titanic", "ocean liner romance and iceberg")]:
            http.put(f"/items/{i}", params=Q, headers=tenant.secret, json={"title": t, "description": d, "properties": {"category": "SciFi", "year": 1986}})
        (top, *_) = http.get("/getRec/content/10/2", params=Q, headers=tenant.public).json()
        assert top["item_id"] == "11" and top["work_id"] == 11
        assert {"title", "description", "genre_1", "author", "year", "url", "price", "score", "explanation"} <= set(top)

    def test_content_for_unknown_item_is_404_not_500(self, http, seeded):
        assert http.get("/getRec/content/ghost/3", params=Q, headers=seeded.public).status_code == 404

    def test_session_accepts_the_new_and_the_deprecated_parameter(self, http, seeded):
        new = http.get("/getRec/session", params={**Q, "viewed_item_ids": "SKU-NIKE-001,SKU-ADIDAS-007", "count": 3}, headers=seeded.public)
        old = http.get("/getRec/session", params={**Q, "viewed_work_ids": "SKU-NIKE-001,SKU-ADIDAS-007", "count": 3}, headers=seeded.public)
        assert new.status_code == old.status_code == 200 and new.json() == old.json()
        assert http.get("/getRec/session", params={**Q, "count": 3}, headers=seeded.public).status_code == 422
        assert http.get("/getRec/session", params={**Q, "viewed_item_ids": "ghost"}, headers=seeded.public).status_code == 404

    def test_session_for_user_is_empty_without_tracked_views(self, http, seeded):
        assert http.get("/getRec/sessionForUser/nobody/3", params=Q, headers=seeded.public).json() == []

    def test_collaborative_needs_a_model_then_works(self, http, seeded, trained):
        assert http.get("/getRec/collaborative/ghost/3", params=Q, headers=trained.public).status_code == 404
        response = http.get("/getRec/collaborative/user_0/3", params=Q, headers=trained.public)
        assert response.status_code == 200 and len(response.json()) == 3

    def test_collaborative_without_a_trained_model_is_404(self, http, seeded):
        track(http, seeded, "purchase", {"user_id": "u", "item_id": "SKU-NIKE-001"})
        assert http.get("/getRec/collaborative/u/3", params=Q, headers=seeded.public).status_code == 404

    def test_hybrid_keeps_alpha(self, http, trained):
        pure_content = http.get("/getRec/hybrid/user_0/SKU-NIKE-001/3", params={**Q, "alpha": 0}, headers=trained.public).json()
        assert "0%" in pure_content[0]["explanation"]["reason"]
        assert http.get("/getRec/hybrid/user_0/SKU-NIKE-001/3", params={**Q, "alpha": 2}, headers=trained.public).status_code == 422

    def test_similar_users_are_visible_to_the_secret_key_only(self, http, trained):
        """`similar_users` names other users - a public key (which lives in web pages) never sees them."""
        secret = http.get("/getRec/collaborative/user_0/3", params=Q, headers=trained.secret).json()
        similar = next(i["explanation"]["similar_users"] for i in secret if i["explanation"].get("similar_users"))
        assert isinstance(similar[0]["user_id"], str) and "shared_item_ids" in similar[0] and "name" in similar[0]

        public = http.get("/getRec/collaborative/user_0/3", params=Q, headers=trained.public).json()
        assert public and all(i["explanation"].get("similar_users") is None for i in public)
        assert "name" not in str(public) or "similar" not in str(public)
        as_object = http.get("/getRec/collaborative/user_0/3", params={**Q, "response_format": "object"}, headers=trained.secret).json()
        assert all(i["explanation"].get("similar_users") is None for i in as_object["items"])


class TestBrowserAccess:
    def test_cors_exposes_the_headers_a_web_page_needs(self, http, seeded):
        response = http.get("/getRec/popular/2", params=Q, headers={**seeded.public, "Origin": "https://shop.example.com"})
        exposed = response.headers["access-control-expose-headers"].lower()
        assert response.headers["access-control-allow-origin"] == "*"
        for header in ("x-recommendation-id", "x-request-id", "x-total-count"):
            assert header in exposed

    def test_preflight_for_tracking_from_a_page_is_allowed(self, http):
        response = http.options("/events/view", params=Q, headers={
            "Origin": "https://shop.example.com", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-api-key,x-request-id",
        })
        assert response.status_code == 200 and response.headers["access-control-allow-origin"] == "*"


ATTRIBUTION_SQL = """
SELECT r.strategy, r.placement,
       count(DISTINCT r.recommendation_id)                               AS recommendations,
       count(i.id) FILTER (WHERE i.event_type = 'impression')           AS impressions,
       count(i.id) FILTER (WHERE i.event_type = 'click')                AS clicks,
       count(i.id) FILTER (WHERE i.event_type = 'purchase')             AS purchases,
       sum((i.properties->>'revenue')::numeric) FILTER (WHERE i.event_type = 'purchase') AS revenue
FROM recommendations r
LEFT JOIN interactions i ON i.recommendation_id = r.recommendation_id AND i.client_id = r.client_id
WHERE r.client_id = :client_id
GROUP BY r.strategy, r.placement
ORDER BY r.placement
"""


def test_the_stored_trace_is_enough_to_compute_ctr_conversion_and_attributed_revenue(http, seeded):
    """The point of recommendation_id: performance per strategy and per placement is one query away."""
    from sqlalchemy import text

    home = get_rec(http, seeded, {"session_id": "s1", "placement": "homepage"}).json()
    product = get_rec(http, seeded, {"session_id": "s1", "item_id": "SKU-NIKE-001", "placement": "product_page"}).json()
    for body in (home, product):
        for item in body["items"][:2]:
            track(http, seeded, "impression", {"session_id": "s1", "item_id": item["item_id"], "recommendation_id": body["recommendation_id"]})
    track(http, seeded, "click", {"session_id": "s1", "item_id": home["items"][0]["item_id"], "recommendation_id": home["recommendation_id"]})
    track(http, seeded, "purchase", {"session_id": "s1", "item_id": home["items"][0]["item_id"], "recommendation_id": home["recommendation_id"], "properties": {"revenue": 120.5}})
    track(http, seeded, "purchase", {"session_id": "s1", "item_id": "SKU-VANS-004", "properties": {"revenue": 999}})  # not attributed: no recommendation_id

    with db.engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(text(ATTRIBUTION_SQL), {"client_id": seeded.client_id})]
    by_placement = {r["placement"]: r for r in rows}
    assert by_placement["homepage"]["strategy"] == "popular"
    assert (by_placement["homepage"]["impressions"], by_placement["homepage"]["clicks"], by_placement["homepage"]["purchases"]) == (2, 1, 1)
    assert float(by_placement["homepage"]["revenue"]) == 120.5
    assert by_placement["product_page"]["strategy"] == "content" and by_placement["product_page"]["impressions"] == 2
    assert by_placement["product_page"]["clicks"] == 0
