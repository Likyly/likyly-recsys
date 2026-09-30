"""Turns a Placement + a runtime context into a ranked list of engine records, by calling
the *existing* recommender.py strategies - this module never computes a recommendation
itself, it only decides which existing function(s) to call and in what order.

Two paths:
  * strategy == "auto": delegates straight to recommend_auto/plan_strategies (untouched) -
    that IS the brief's "auto/orchestrated mode": not a new ML model, just the existing
    signal-based selection recommender.py already had before Placements existed.
  * an explicit strategy (content/session/collaborative/hybrid/popular): resolves the
    context's ids the same way recommend_auto does internally (read-only - an id LIKYLY has
    never seen contributes no signal, never an error), runs that one strategy, and falls back
    to `fallback_strategy` (then always to "popular") if it comes back empty - mirroring
    plan_strategies' own "popular is always last" guarantee instead of ever raising for "no
    results".

Deliberately a separate, small id-resolution helper (resolve_context_signals below) rather
than refactoring recommend_auto's internals to share it: recommend_auto is exercised by the
existing /getRec test suite and kept exactly as-is on purpose - the two paths call the same
underlying primitives (resolve_internal_ids, get_data, get_recent_viewed_work_ids(_for_session))
without one depending on the other's internals.
"""
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

from exploreData import get_data  # noqa: E402
from db import (  # noqa: E402
    KIND_ITEM, KIND_USER, count_user_signal_interactions, get_recent_viewed_work_ids,
    get_recent_viewed_work_ids_for_session, resolve_internal_ids,
)

import recommender as rec  # noqa: E402

NAMED_STRATEGIES = ("content", "session", "collaborative", "hybrid", "popular")
ALL_STRATEGIES = NAMED_STRATEGIES + ("auto",)


@dataclass
class ResolvedSignals:
    internal_user: Optional[int]
    anchor: Optional[int]
    history: list[int]
    exclude: set[int]
    user_has_signal: bool


def _effective_session_id(context: dict[str, Any]) -> Optional[str]:
    """anonymous_id is functionally the same role session_id plays for an anonymous
    visitor - used only when the caller didn't send session_id itself."""
    return context.get("session_id") or context.get("anonymous_id")


def resolve_context_signals(client_id: int, product_type: str, context: dict[str, Any]) -> ResolvedSignals:
    user_id = context.get("user_id")
    session_id = _effective_session_id(context)
    item_id = context.get("current_item_id")

    internal_user = resolve_internal_ids(client_id, product_type, KIND_USER, [user_id]).get(user_id) if user_id else None
    catalog_ids = set(get_data(product_type, None, None, client_id=client_id)["work_id"].tolist())

    anchor = None
    if item_id:
        candidate = resolve_internal_ids(client_id, product_type, KIND_ITEM, [item_id]).get(item_id)
        anchor = candidate if candidate in catalog_ids else None

    history: list[int] = []
    if internal_user is not None:
        history = get_recent_viewed_work_ids(client_id, product_type, internal_user, limit=10)
    if not history and session_id:
        history = get_recent_viewed_work_ids_for_session(client_id, product_type, session_id, limit=10)

    user_has_signal = internal_user is not None and count_user_signal_interactions(client_id, product_type, internal_user) > 0
    exclude = set(history) | ({anchor} if anchor is not None else set())
    return ResolvedSignals(internal_user, anchor, history, exclude, user_has_signal)


def _run_named_strategy(strategy: str, client_id: str, product_type: str, count: int, signals: ResolvedSignals) -> list[dict]:
    if strategy == "content":
        return rec.rec_content(client_id, product_type, signals.anchor, count) if signals.anchor is not None else []
    if strategy == "session":
        return rec.rec_session(client_id, product_type, signals.history, count) if signals.history else []
    if strategy == "collaborative":
        if signals.internal_user is None:
            return []
        try:
            return rec.rec_collaborative(client_id, product_type, signals.internal_user, count)
        except (FileNotFoundError, IndexError, ValueError):
            return []  # no trained model yet, or a user it has never seen
    if strategy == "hybrid":
        return rec.rec_hybrid(client_id, product_type, signals.internal_user, signals.anchor, count) if signals.anchor is not None else []
    if strategy == "popular":
        return rec.rec_popular(client_id, product_type, count, exclude_work_ids=signals.exclude, catalog_fallback=True)
    raise ValueError(f"Unknown strategy '{strategy}' - one of {NAMED_STRATEGIES}")


@dataclass
class PlacementResult:
    strategy_used: str
    records: list[dict]
    attempted: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def missing_required_signals(placement: dict[str, Any], context: dict[str, Any]) -> list[str]:
    """Which of placement.signals["required"] the caller didn't send - the only signal
    validation Placements do; everything else is optional by construction."""
    required = (placement.get("signals") or {}).get("required") or []
    return [key for key in required if not context.get(key)]


def run_placement(placement: dict[str, Any], context: dict[str, Any], count: int, *, client_id: int) -> PlacementResult:
    """Assumes missing_required_signals(placement, context) is already empty - callers
    (placement_service.py) validate before running, so this never has to decide what "missing
    a required signal" means at execution time."""
    product_type = placement["product_type"]
    strategy = placement["strategy"]

    if strategy == "auto":
        result = rec.recommend_auto(
            client_id, product_type, user_id=context.get("user_id"), session_id=_effective_session_id(context),
            item_id=context.get("current_item_id"), viewed_item_ids=[], count=count,
        )
        return PlacementResult(result.strategy, result.records, result.attempted)

    signals = resolve_context_signals(client_id, product_type, context)
    attempted = [strategy]
    warnings: list[str] = []
    records = _run_named_strategy(strategy, client_id, product_type, count, signals)
    strategy_used = strategy

    fallback = placement.get("fallback_strategy")
    if not records and fallback:
        attempted.append(fallback)
        if fallback == "auto":
            result = rec.recommend_auto(
                client_id, product_type, user_id=context.get("user_id"), session_id=_effective_session_id(context),
                item_id=context.get("current_item_id"), viewed_item_ids=[], count=count,
            )
            records, strategy_used = result.records, result.strategy
            attempted += result.attempted
            warnings.append(f"strategy '{strategy}' returned no results - used fallback_strategy 'auto' (resolved to '{result.strategy}')")
        else:
            records = _run_named_strategy(fallback, client_id, product_type, count, signals)
            strategy_used = fallback
            warnings.append(f"strategy '{strategy}' returned no results - used fallback_strategy '{fallback}'")

    if not records and strategy_used != "popular":
        attempted.append("popular")
        records = rec.rec_popular(client_id, product_type, count, exclude_work_ids=signals.exclude, catalog_fallback=True)
        strategy_used = "popular"
        warnings.append(f"'{attempted[-2]}' returned no results - used the final 'popular' fallback")

    return PlacementResult(strategy_used, records, attempted, warnings)
