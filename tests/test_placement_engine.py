"""Unit-level tests for placement_engine.py's own logic - missing_required_signals and the
explicit-strategy fallback chain - independent of the HTTP layer (test_placements.py covers
the full stack end to end)."""
import placement_engine as engine


def test_missing_required_signals():
    placement = {"signals": {"required": ["current_item_id", "user_id"], "optional": []}}
    assert engine.missing_required_signals(placement, {}) == ["current_item_id", "user_id"]
    assert engine.missing_required_signals(placement, {"current_item_id": "a"}) == ["user_id"]
    assert engine.missing_required_signals(placement, {"current_item_id": "a", "user_id": "b"}) == []


def test_missing_required_signals_ignores_falsy_but_present_values():
    """An empty string / empty list counts as "not sent" - a placement author shouldn't have
    to special-case "sent but empty" versus "not sent at all"."""
    placement = {"signals": {"required": ["cart_item_ids"], "optional": []}}
    assert engine.missing_required_signals(placement, {"cart_item_ids": []}) == ["cart_item_ids"]


def test_no_required_signals_is_never_missing_anything():
    placement = {"signals": {}}
    assert engine.missing_required_signals(placement, {}) == []


def test_run_placement_auto_delegates_to_recommend_auto(tenant, monkeypatch):
    calls = []

    class FakeResult:
        strategy = "popular"
        records = [{"work_id": 1, "title": "x"}]
        attempted = ["popular"]

    def fake_recommend_auto(client_id, product_type, **kwargs):
        calls.append(kwargs)
        return FakeResult()

    monkeypatch.setattr("recommender.recommend_auto", fake_recommend_auto)
    placement = {"strategy": "auto", "product_type": "shop", "fallback_strategy": None}
    result = engine.run_placement(placement, {"session_id": "s1"}, 5, client_id=tenant.client_id)

    assert result.strategy_used == "popular"
    assert calls[0]["session_id"] == "s1"


def test_run_placement_explicit_strategy_uses_fallback_then_popular(tenant, monkeypatch):
    monkeypatch.setattr(engine, "resolve_context_signals", lambda *a, **k: engine.ResolvedSignals(
        internal_user=None, anchor=None, history=[], exclude=set(), user_has_signal=False,
    ))
    monkeypatch.setattr("recommender.rec_content", lambda *a, **k: [])
    monkeypatch.setattr("recommender.rec_session", lambda *a, **k: [])
    monkeypatch.setattr("recommender.rec_popular", lambda *a, **k: [{"work_id": 1, "title": "fallback"}])

    placement = {"strategy": "content", "product_type": "shop", "fallback_strategy": "session"}
    result = engine.run_placement(placement, {}, 5, client_id=tenant.client_id)

    assert result.attempted == ["content", "session", "popular"]
    assert result.strategy_used == "popular"
    assert len(result.warnings) == 2  # fallback tried, then the final popular safety net


def test_run_placement_explicit_strategy_succeeds_without_needing_a_fallback(tenant, monkeypatch):
    monkeypatch.setattr(engine, "resolve_context_signals", lambda *a, **k: engine.ResolvedSignals(
        internal_user=None, anchor=1, history=[], exclude=set(), user_has_signal=False,
    ))
    monkeypatch.setattr("recommender.rec_content", lambda *a, **k: [{"work_id": 2, "title": "ok"}])

    placement = {"strategy": "content", "product_type": "shop", "fallback_strategy": "popular"}
    result = engine.run_placement(placement, {"current_item_id": "a"}, 5, client_id=tenant.client_id)

    assert result.attempted == ["content"]
    assert result.strategy_used == "content"
    assert result.warnings == []
