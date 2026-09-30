"""GET /clients/me/analytics/summary and /detail - the "Performance" page's data, Free vs Pro.

Self-service (Supabase-session) only; same fake-JWT technique as test_workspace.py.
"""
import uuid
from datetime import date, timedelta

import pytest

import app as app_module
import db


@pytest.fixture
def workspace(monkeypatch, http):
    uid = f"supabase-user-analytics-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": uid, "email": "analytics@example.com"})
    auth = {"Authorization": "Bearer fake"}
    created = http.post("/clients/me", headers=auth).json()
    secret = {"X-API-Key": created["secret_key"]}
    # "popular" (the auto-strategy's fallback with no signals) needs at least one catalog item
    # to actually return something for _seed_activity's recommend call to attribute events to.
    item = http.put("/items/sku-1", params={"data_product_type": "shop"}, headers=secret, json={"title": "Chaussure", "properties": {"price": 29.9}})
    assert item.status_code == 201, item.text
    return {"auth": auth, "secret": secret, "public": {"X-API-Key": created["public_key"]}, "client_id": created["client_id"]}


def _create_placement(http, secret, slug="pdp-related", **overrides):
    body = {
        "slug": slug, "name": "Related products", "context_type": "product_page",
        "product_type": "shop", "limit": 4, "strategy": "auto",
        "signals": {"required": [], "optional": ["session_id"]},
        **overrides,
    }
    response = http.post("/placements", headers=secret, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _seed_activity(http, public, slug):
    # One recommend call, one impression, one click, one attributed purchase.
    recommend = http.post(f"/placements/{slug}/recommend", headers=public, json={"context": {"session_id": "sess_1"}})
    assert recommend.status_code == 200, recommend.text
    recommendation_id = recommend.json()["recommendation_id"]
    item_id = recommend.json()["items"][0]["item_id"]

    for event_type in ("impression", "click"):
        r = http.post(
            f"/events/{event_type}", headers=public,
            params={"data_product_type": "shop"},
            json={"item_id": item_id, "session_id": "sess_1", "placement": slug, "recommendation_id": recommendation_id},
        )
        assert r.status_code == 200, r.text

    r = http.post(
        "/events/purchase", headers=public, params={"data_product_type": "shop"},
        json={
            "item_id": item_id, "session_id": "sess_1", "placement": slug, "recommendation_id": recommendation_id,
            "event_id": f"purchase_{uuid.uuid4().hex[:8]}", "properties": {"price": 29.9},
        },
    )
    assert r.status_code == 200, r.text


def test_summary_reflects_seeded_activity(http, workspace):
    _create_placement(http, workspace["secret"])
    _seed_activity(http, workspace["public"], "pdp-related")

    response = http.get("/clients/me/analytics/summary", headers=workspace["auth"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recommendations_served"] == 1
    assert body["impressions"] == 1
    assert body["clicks"] == 1
    assert body["ctr"] == 1.0


def test_free_plan_summary_window_is_clamped_to_seven_days(http, workspace):
    db.set_client_plan(workspace["client_id"], db.PLAN_FREE)
    today = date.today()
    start = today - timedelta(days=89)
    response = http.get("/clients/me/analytics/summary", params={"start": start.isoformat(), "end": today.isoformat()}, headers=workspace["auth"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["end"] == today.isoformat()
    assert date.fromisoformat(body["start"]) == today - timedelta(days=6)  # pushed forward to a 7-day span


def test_pro_plan_summary_window_can_go_up_to_ninety_days(http, workspace):
    db.set_client_plan(workspace["client_id"], db.PLAN_PRO)
    today = date.today()
    start = today - timedelta(days=89)
    response = http.get("/clients/me/analytics/summary", params={"start": start.isoformat(), "end": today.isoformat()}, headers=workspace["auth"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["start"] == start.isoformat()
    assert body["end"] == today.isoformat()


def test_summary_defaults_to_last_seven_days_with_no_dates_given(http, workspace):
    today = date.today()
    response = http.get("/clients/me/analytics/summary", headers=workspace["auth"])
    body = response.json()
    assert body["end"] == today.isoformat()
    assert date.fromisoformat(body["start"]) == today - timedelta(days=6)


def test_a_past_date_range_is_honored_not_just_the_last_n_days(http, workspace):
    # start/end past-dated relative to today - not seeded activity, just confirms the endpoint
    # actually uses the requested window instead of always defaulting to "now".
    db.set_client_plan(workspace["client_id"], db.PLAN_PRO)
    start, end = "2020-01-01", "2020-01-10"
    response = http.get("/clients/me/analytics/summary", params={"start": start, "end": end}, headers=workspace["auth"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["start"], body["end"]) == (start, end)
    assert body["recommendations_served"] == 0  # nothing happened in Jan 2020 for this brand-new tenant


def test_detail_is_forbidden_on_free_plan(http, workspace):
    db.set_client_plan(workspace["client_id"], db.PLAN_FREE)
    response = http.get("/clients/me/analytics/detail", headers=workspace["auth"])
    assert response.status_code == 403
    assert "plan limit" in response.json()["detail"].lower()


def test_detail_reports_timeseries_by_placement_and_revenue_on_pro(http, workspace):
    db.set_client_plan(workspace["client_id"], db.PLAN_PRO)
    _create_placement(http, workspace["secret"])
    _seed_activity(http, workspace["public"], "pdp-related")

    response = http.get("/clients/me/analytics/detail", headers=workspace["auth"])
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["timeseries"]) == 1
    today = body["timeseries"][0]
    assert (today["recommendations_served"], today["impressions"], today["clicks"]) == (1, 1, 1)

    assert body["by_placement"] == [
        {"placement": "pdp-related", "recommendations_served": 1, "impressions": 1, "clicks": 1, "ctr": 1.0}
    ]

    assert body["revenue"] == {"purchases": 1, "revenue": 29.9}


def test_unattributed_purchases_are_excluded_from_revenue(http, workspace):
    # A purchase with no recommendation_id (not driven by a recommendation) must not count.
    db.set_client_plan(workspace["client_id"], db.PLAN_PRO)

    r = http.post(
        "/events/purchase", headers=workspace["public"], params={"data_product_type": "shop"},
        json={"item_id": "sku-1", "session_id": "sess_2", "event_id": f"purchase_{uuid.uuid4().hex[:8]}", "properties": {"price": 500}},
    )
    assert r.status_code == 200, r.text

    response = http.get("/clients/me/analytics/detail", headers=workspace["auth"])
    assert response.json()["revenue"] == {"purchases": 0, "revenue": 0.0}


def test_requires_a_session(http):
    assert http.get("/clients/me/analytics/summary").status_code == 401
    assert http.get("/clients/me/analytics/detail").status_code == 401
