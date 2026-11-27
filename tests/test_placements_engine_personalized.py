"""strategy "personalized": the visitor's history decides, never the product on the page."""
import placement_engine as engine


def test_personalized_never_anchors_on_the_current_item(monkeypatch):
    calls = []

    class Result:
        strategy, records, attempted = "session_history", [{"work_id": 1}], ["session_history"]

    def fake_auto(client_id, product_type, **kwargs):
        calls.append(kwargs)
        return Result()

    monkeypatch.setattr(engine.rec, "recommend_auto", fake_auto)
    placement = {"product_type": "shop", "strategy": "personalized"}
    result = engine.run_placement(placement, {"current_item_id": "SKU-1", "session_id": "s1", "user_id": "u1"}, 4, client_id=1)
    assert result.strategy_used == "session_history"
    assert calls[0]["item_id"] is None and calls[0]["user_id"] == "u1"

    engine.run_placement({"product_type": "shop", "strategy": "auto"}, {"current_item_id": "SKU-1"}, 4, client_id=1)
    assert calls[1]["item_id"] == "SKU-1"  # auto still anchors on it


def test_personalized_is_an_accepted_strategy(http, tenant):
    body = {"slug": "for-you", "name": "For you", "context_type": "product_page", "product_type": "shop", "limit": 4,
            "strategy": "personalized", "fallback_strategy": "popular", "signals": {"required": [], "optional": ["user_id", "session_id"]}}
    assert http.post("/placements", headers=tenant.secret, json=body).status_code == 201
