"""GET /analytics/* - the key-authenticated performance numbers (secret key, or a developer key
with `analytics:read`), e.g. for the WordPress plugin. Same plan rules as the console's
/clients/me/analytics/*: window clamped to the plan, per-placement breakdown on every plan,
detail and top items Pro only."""
import uuid
from datetime import date, timedelta

import pytest

import db
from conftest import Q, make_tenant

ENDPOINTS = ["/analytics/summary", "/analytics/placements", "/analytics/detail", "/analytics/items", "/analytics/plan"]


def _placement(http, tenant, slug):
    response = http.post("/placements", headers=tenant.secret, json={
        "slug": slug, "name": slug, "context_type": "product_page", "product_type": "shop",
        "limit": 4, "strategy": "auto", "signals": {"required": [], "optional": ["session_id"]},
    })
    assert response.status_code == 201, response.text


def _recommend(http, tenant, slug):
    response = http.post(f"/placements/{slug}/recommend", headers=tenant.public, json={"context": {"session_id": "sess_1"}})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["recommendation_id"], [item["item_id"] for item in body["items"]]


def _event(http, tenant, event_type, item_id, recommendation_id=None, placement=None, price=None, quantity=None):
    payload = {"item_id": item_id, "session_id": "sess_1"}
    if recommendation_id:
        payload["recommendation_id"] = recommendation_id
    if placement:
        payload["placement"] = placement
    if event_type == "purchase":
        payload["event_id"] = f"purchase_{uuid.uuid4().hex[:8]}"
    if price is not None:
        payload["properties"] = {"price": price}
    if quantity is not None:
        payload["quantity"] = quantity
    response = http.post(f"/events/{event_type}", headers=tenant.public, params=Q, json=payload)
    assert response.status_code == 200, response.text


@pytest.fixture
def pro(http, seeded):
    db.set_client_plan(seeded.client_id, db.PLAN_PRO)
    return seeded


# --- auth -----------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ENDPOINTS)
def test_secret_key_reads_every_endpoint(http, pro, path):
    assert http.get(path, headers=pro.secret).status_code == 200


@pytest.mark.parametrize("path", ENDPOINTS)
def test_public_key_is_refused(http, pro, path):
    response = http.get(path, headers=pro.public)
    assert response.status_code == 403
    assert "analytics:read" in response.json()["detail"]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_missing_or_unknown_key_is_401(http, path):
    assert http.get(path).status_code == 401
    assert http.get(path, headers={"X-API-Key": "lk_not_a_real_key"}).status_code == 401


@pytest.mark.parametrize("path", ENDPOINTS)
def test_developer_key_needs_the_analytics_read_scope(http, pro, path):
    _, granted = db.create_developer_key(pro.client_id, "plugin", ["analytics:read"])
    _, other = db.create_developer_key(pro.client_id, "agent", ["placements:read", "sources:read"])
    assert http.get(path, headers={"X-API-Key": granted}).status_code == 200
    assert http.get(path, headers={"X-API-Key": other}).status_code == 403


def test_analytics_read_is_a_mintable_scope(http, monkeypatch):
    import app as app_module
    uid = f"sb-analytics-{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": uid, "email": "a@example.com"})
    auth = {"Authorization": "Bearer fake"}
    http.post("/clients/me", headers=auth)
    created = http.post("/clients/me/developer-keys", headers=auth, json={"name": "wp", "scopes": ["analytics:read"]})
    assert created.status_code in (200, 201), created.text
    assert http.get("/analytics/summary", headers={"X-API-Key": created.json()["key"]}).status_code == 200


# --- window and plan gating -----------------------------------------------------------------

def test_free_window_is_clamped_to_seven_days_and_says_so(http, seeded):
    db.set_client_plan(seeded.client_id, db.PLAN_FREE)
    today = date.today()
    body = http.get("/analytics/summary", headers=seeded.secret, params={"start": (today - timedelta(days=60)).isoformat(), "end": today.isoformat()}).json()
    assert body["end"] == today.isoformat()
    assert body["start"] == (today - timedelta(days=6)).isoformat()
    assert (body["plan"], body["window_days"], body["max_window_days"]) == ("free", 7, 7)


def test_pro_window_goes_up_to_ninety_days(http, pro):
    today = date.today()
    body = http.get("/analytics/placements", headers=pro.secret, params={"start": (today - timedelta(days=200)).isoformat(), "end": today.isoformat()}).json()
    assert body["start"] == (today - timedelta(days=89)).isoformat()
    assert (body["plan"], body["window_days"], body["max_window_days"]) == ("pro", 90, 90)


def test_summary_defaults_to_seven_days(http, pro):
    body = http.get("/analytics/summary", headers=pro.secret).json()
    assert body["window_days"] == 7 and body["end"] == date.today().isoformat()


def test_bad_date_is_422(http, pro):
    assert http.get("/analytics/summary", headers=pro.secret, params={"start": "yesterday"}).status_code == 422
    assert http.get("/analytics/items", headers=pro.secret, params={"limit": 51}).status_code == 422
    assert http.get("/analytics/items", headers=pro.secret, params={"limit": 0}).status_code == 422


@pytest.mark.parametrize("path", ["/analytics/detail", "/analytics/items"])
def test_detail_and_items_are_pro_only(http, seeded, path):
    db.set_client_plan(seeded.client_id, db.PLAN_FREE)
    response = http.get(path, headers=seeded.secret)
    assert response.status_code == 403
    assert response.json()["detail"].startswith("Free plan limit reached:")
    assert db.UPGRADE_URL in response.json()["detail"]


def test_placements_summary_and_plan_are_available_on_free(http, seeded):
    db.set_client_plan(seeded.client_id, db.PLAN_FREE)
    _placement(http, seeded, "pdp")
    recommendation_id, items = _recommend(http, seeded, "pdp")
    _event(http, seeded, "impression", items[0], recommendation_id, "pdp")
    _event(http, seeded, "click", items[0], recommendation_id, "pdp")

    summary = http.get("/analytics/summary", headers=seeded.secret).json()
    assert (summary["recommendations_served"], summary["impressions"], summary["clicks"], summary["ctr"]) == (1, 1, 1, 1.0)

    placements = http.get("/analytics/placements", headers=seeded.secret)
    assert placements.status_code == 200
    assert placements.json()["placements"] == [{
        "placement": "pdp", "recommendations_served": 1, "impressions": 1, "clicks": 1, "ctr": 1.0,
        "purchases": 0, "revenue": 0.0,
    }]


def test_plan_reports_limits_and_usage(http, seeded):
    db.set_client_plan(seeded.client_id, db.PLAN_FREE)
    body = http.get("/analytics/plan", headers=seeded.secret).json()
    assert (body["plan"], body["plan_name"]) == ("free", "Free")
    assert body["limits"]["product_limit"] == 50 and body["limits"]["analytics_detail"] is False
    assert body["limits"]["analytics_window_days"] == 7
    assert body["usage"]["product_count"] == 8 and body["usage"]["product_limit"] == 50
    assert body["usage"]["index_count"] == 1 and body["usage"]["index_limit"] == 1
    assert body["upgrade_url"] == db.UPGRADE_URL


# --- attribution ----------------------------------------------------------------------------

def test_purchases_and_revenue_are_attributed_per_placement(http, pro):
    _placement(http, pro, "pdp")
    _placement(http, pro, "cart")
    pdp_rec, pdp_items = _recommend(http, pro, "pdp")
    cart_rec, cart_items = _recommend(http, pro, "cart")

    # Credited through the recommendation's placement, even without `placement` on the event.
    _event(http, pro, "purchase", pdp_items[0], pdp_rec, price=30.0, quantity=2)
    _event(http, pro, "purchase", pdp_items[1], pdp_rec, placement="pdp", price=10.5)
    _event(http, pro, "purchase", cart_items[0], cart_rec, placement="cart", price=5)
    # A recommendation with no placement (plain POST /getRec): the purchase's own placement counts.
    plain = http.post("/getRec", headers=pro.public, params=Q, json={"count": 3}).json()
    _event(http, pro, "purchase", plain["items"][0]["item_id"], plain["recommendation_id"], placement="footer", price=7)
    # Not attributable to a recommendation: never counted.
    _event(http, pro, "purchase", pdp_items[0], placement="pdp", price=999)

    rows = {row["placement"]: row for row in http.get("/analytics/placements", headers=pro.secret).json()["placements"]}
    assert (rows["pdp"]["purchases"], rows["pdp"]["revenue"]) == (2, 70.5)
    assert (rows["cart"]["purchases"], rows["cart"]["revenue"]) == (1, 5.0)
    assert (rows["footer"]["purchases"], rows["footer"]["revenue"]) == (1, 7.0)
    assert rows["footer"]["recommendations_served"] == 0
    assert rows["pdp"]["recommendations_served"] == 1

    detail = http.get("/analytics/detail", headers=pro.secret).json()
    assert detail["revenue"] == {"purchases": 4, "revenue": 82.5}
    assert detail["window_days"] == 30
    assert sum(point["recommendations_served"] for point in detail["timeseries"]) == 3


def test_top_items_are_ranked_by_clicks_then_purchases(http, pro):
    _placement(http, pro, "pdp")
    recommendation_id, items = _recommend(http, pro, "pdp")
    first, second, third = items[0], items[1], items[2]
    for _ in range(2):
        _event(http, pro, "impression", second, recommendation_id, "pdp")
        _event(http, pro, "click", second, recommendation_id, "pdp")
    _event(http, pro, "impression", first, recommendation_id, "pdp")
    _event(http, pro, "click", first, recommendation_id, "pdp")
    _event(http, pro, "purchase", first, recommendation_id, "pdp", price=100.0)
    _event(http, pro, "impression", third, recommendation_id, "pdp")
    _event(http, pro, "click", third, recommendation_id, "pdp")
    # Unattributed click: ignored.
    _event(http, pro, "click", third, placement="pdp")

    body = http.get("/analytics/items", headers=pro.secret, params={"limit": 3}).json()
    ranked = body["items"]
    assert [row["item_id"] for row in ranked] == [second, first, third]
    top = ranked[0]
    assert (top["clicks"], top["impressions"], top["ctr"], top["purchases"], top["recommended"]) == (2, 2, 1.0, 0, 1)
    assert (ranked[1]["purchases"], ranked[1]["revenue"]) == (1, 100.0)
    assert ranked[2]["clicks"] == 1
    titles = {row["item_id"]: row["title"] for row in ranked}
    assert all(titles.values()) and all(row["product_type"] == "shop" for row in ranked)

    everything = http.get("/analytics/items", headers=pro.secret, params={"limit": 50}).json()["items"]
    # Items only recommended (no event) still show, after the ones with activity.
    assert len(everything) == len(items)
    assert all(row["recommended"] == 1 for row in everything)


# --- isolation ------------------------------------------------------------------------------

def test_one_client_never_sees_another_clients_numbers(http, pro):
    _placement(http, pro, "pdp")
    recommendation_id, items = _recommend(http, pro, "pdp")
    _event(http, pro, "impression", items[0], recommendation_id, "pdp")
    _event(http, pro, "click", items[0], recommendation_id, "pdp")
    _event(http, pro, "purchase", items[0], recommendation_id, "pdp", price=50.0)

    other = make_tenant(db.PLAN_PRO)
    http.put("/items/other-1", params=Q, headers=other.secret, json={"title": "Other"})
    # The other client reuses the same recommendation_id on its own events: still nothing leaks.
    _event(http, other, "purchase", "other-1", recommendation_id, price=1000.0)

    summary = http.get("/analytics/summary", headers=other.secret).json()
    assert (summary["recommendations_served"], summary["impressions"], summary["clicks"]) == (0, 0, 0)
    assert http.get("/analytics/placements", headers=other.secret).json()["placements"] == []
    assert http.get("/analytics/detail", headers=other.secret).json()["revenue"] == {"purchases": 1, "revenue": 1000.0}
    other_items = http.get("/analytics/items", headers=other.secret).json()["items"]
    assert [row["item_id"] for row in other_items] == ["other-1"]
    assert other_items[0]["recommended"] == 0
    assert http.get("/analytics/plan", headers=other.secret).json()["usage"]["product_count"] == 1

    mine = http.get("/analytics/detail", headers=pro.secret).json()["revenue"]
    assert mine == {"purchases": 1, "revenue": 50.0}
    my_items = http.get("/analytics/items", headers=pro.secret).json()["items"]
    assert "other-1" not in {row["item_id"] for row in my_items}
