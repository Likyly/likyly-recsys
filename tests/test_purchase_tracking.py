"""GET /clients/me/purchase-tracking - the console's "Suivi des achats" panel.

Counts, on any plan: purchases received, those carrying a recommendation_id and a numeric price
(what the attributed revenue needs), add to cart and clicks on recommendations.
"""
import uuid

import pytest

import app as app_module
import db


@pytest.fixture
def workspace(monkeypatch, http):
    uid = f"supabase-user-purchases-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": uid, "email": "purchases@example.com"})
    auth = {"Authorization": "Bearer fake"}
    created = http.post("/clients/me", headers=auth).json()
    secret = {"X-API-Key": created["secret_key"]}
    item = http.put("/items/sku-1", params={"data_product_type": "shop"}, headers=secret, json={"title": "Chaussure"})
    assert item.status_code == 201, item.text
    placement = http.post("/placements", headers=secret, json={
        "slug": "pdp-related", "name": "Related", "context_type": "product_page", "product_type": "shop",
        "limit": 4, "strategy": "auto", "signals": {"required": [], "optional": ["session_id"]},
    })
    assert placement.status_code == 201, placement.text
    return {"auth": auth, "public": {"X-API-Key": created["public_key"]}, "client_id": created["client_id"]}


def _event(http, public, event_type, **body):
    r = http.post(f"/events/{event_type}", headers=public, params={"data_product_type": "shop"}, json={"item_id": "sku-1", "session_id": "sess_1", **body})
    assert r.status_code == 200, r.text


def test_nothing_received_yet(http, workspace):
    r = http.get("/clients/me/purchase-tracking", headers=workspace["auth"])
    assert r.status_code == 200, r.text
    assert r.json() == {
        "window_days": 30, "purchases": 0, "attributed_purchases": 0, "purchases_with_price": 0,
        "add_to_cart": 0, "recommendation_clicks": 0, "last_purchase_at": None,
    }


def test_counts_what_the_attributed_revenue_needs(http, workspace):
    recommend = http.post("/placements/pdp-related/recommend", headers=workspace["public"], json={"context": {"session_id": "sess_1"}})
    assert recommend.status_code == 200, recommend.text
    rec = recommend.json()["recommendation_id"]

    _event(http, workspace["public"], "click", recommendation_id=rec, placement="pdp-related")
    _event(http, workspace["public"], "add_to_cart", recommendation_id=rec)
    # Counted in the revenue: recommendation_id and a numeric price.
    _event(http, workspace["public"], "purchase", recommendation_id=rec, event_id="p1", properties={"price": 49.9, "currency": "EUR"})
    # Received, but not attributable: no recommendation_id.
    _event(http, workspace["public"], "purchase", event_id="p2", properties={"price": 20})
    # Attributed, but adds nothing: the price is a string.
    _event(http, workspace["public"], "purchase", recommendation_id=rec, event_id="p3", properties={"price": "19.90"})

    body = http.get("/clients/me/purchase-tracking", headers=workspace["auth"]).json()
    assert body["purchases"] == 3
    assert body["attributed_purchases"] == 2
    assert body["purchases_with_price"] == 2
    assert body["add_to_cart"] == 1
    assert body["recommendation_clicks"] == 1
    assert body["last_purchase_at"] is not None


def test_available_on_the_free_plan(http, workspace):
    db.set_client_plan(workspace["client_id"], db.PLAN_FREE)
    _event(http, workspace["public"], "purchase", event_id="p1", properties={"price": 10})
    r = http.get("/clients/me/purchase-tracking", headers=workspace["auth"], params={"days": 7})
    assert r.status_code == 200, r.text
    assert r.json()["window_days"] == 7
    assert r.json()["purchases"] == 1


def test_window_is_bounded(http, workspace):
    assert http.get("/clients/me/purchase-tracking", headers=workspace["auth"], params={"days": 0}).status_code == 422
    assert http.get("/clients/me/purchase-tracking", headers=workspace["auth"], params={"days": 91}).status_code == 422


def test_needs_a_session(http):
    assert http.get("/clients/me/purchase-tracking").status_code == 401
