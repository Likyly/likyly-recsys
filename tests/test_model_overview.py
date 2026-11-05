"""GET /models/overview - the read-only model report behind the MCP tool get_model_overview
and the /likyly-model command: trained versions (which one is live) and production
performance per engine. Readable with a developer key (placements:read), never the public key.
"""
import uuid

import app as app_module
import db
from conftest import Q

PDP = {
    "slug": "pdp-overview", "name": "Related", "context_type": "product_page", "product_type": "shop",
    "limit": 3, "strategy": "content", "signals": {"required": ["current_item_id"], "optional": []},
}


def _serve_and_track(http, seeded):
    assert http.post("/placements", headers=seeded.secret, json=PDP).status_code == 201
    response = http.post(
        "/placements/pdp-overview/recommend", headers=seeded.public,
        json={"context": {"current_item_id": "SKU-NIKE-001", "session_id": "s1"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    item_id = body["items"][0]["item_id"]
    for event_type in ("impression", "impression", "click", "add_to_cart"):
        event = http.post(
            f"/events/{event_type}", params=Q, headers=seeded.public,
            json={"item_id": item_id, "session_id": "s1", "placement": "pdp-overview", "recommendation_id": body["recommendation_id"]},
        )
        assert event.status_code == 200, event.text
    return body["strategy_used"]


def test_overview_lists_versions_and_marks_the_live_one(http, seeded):
    live = db.record_model_version(seeded.client_id, "shop", "/srv/a.npz", 64, 0.05, 20, precision_at_k=0.21, num_users=40, num_items=8, num_interactions=300)
    db.promote_model_version(live, seeded.client_id, "shop")
    db.record_model_version(seeded.client_id, "shop", "/srv/b.npz", 64, 0.05, 20, precision_at_k=0.18, num_users=41, num_items=8, num_interactions=310)

    body = http.get("/models/overview", params=Q, headers=seeded.secret).json()

    assert body["active_version"]["id"] == live
    assert [v["is_active"] for v in body["versions"]] == [False, True]  # newest first: the rejected candidate
    assert body["precision_k"] == 10
    assert "file_path" not in body["active_version"]  # a server path is never part of the contract
    assert all("file_path" not in v for v in body["versions"])


def test_overview_counts_production_traffic_per_engine(http, seeded):
    strategy = _serve_and_track(http, seeded)

    body = http.get("/models/overview", params={**Q, "days": 7}, headers=seeded.secret).json()

    assert body["window_days"] == 7
    rows = {row["strategy"]: row for row in body["performance"]}
    assert strategy in rows, body
    row = rows[strategy]
    assert (row["recommendations_served"], row["impressions"], row["clicks"], row["add_to_carts"], row["purchases"]) == (1, 2, 1, 1, 0)
    assert row["ctr"] == 0.5


def test_overview_without_a_model_or_traffic_is_empty_not_an_error(http, seeded):
    body = http.get("/models/overview", params=Q, headers=seeded.secret).json()
    assert body["active_version"] is None and body["versions"] == [] and body["performance"] == []


def test_overview_is_readable_with_a_developer_key_but_not_the_public_key(http, seeded, monkeypatch):
    uid = f"supabase-overview-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": uid, "email": "x@example.com"})
    auth = {"Authorization": "Bearer fake"}
    http.post("/clients/me", headers=auth)
    read_only = http.post("/clients/me/developer-keys", headers=auth, json={"name": "ro", "scopes": ["placements:read"]}).json()
    sources_only = http.post("/clients/me/developer-keys", headers=auth, json={"name": "src", "scopes": ["sources:read"]}).json()

    assert http.get("/models/overview", params=Q, headers={"X-API-Key": read_only["key"]}).status_code == 200
    assert http.get("/models/overview", params=Q, headers={"X-API-Key": sources_only["key"]}).status_code == 403
    assert http.get("/models/overview", params=Q, headers=seeded.public).status_code == 403
    # Training stays secret-key only.
    assert http.get("/generateModel", params=Q, headers={"X-API-Key": read_only["key"]}).status_code in (401, 403)
