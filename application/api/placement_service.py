"""Business logic behind the /placements* routes - mirrors data_source_service.py's split:
routes in placements.py stay thin, this module decides what create/preview/recommend mean.
"""
import os
import sys
from datetime import timedelta
from typing import Any, Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

import db  # noqa: E402

import placement_engine as engine  # noqa: E402
from placement_filters import apply_filters  # noqa: E402


class NotFoundError(Exception):
    """No such placement for this account - the route layer turns this into a 404,
    deliberately indistinguishable from "never existed" (see db.py's placement functions,
    all client_id-scoped)."""


class ValidationError(Exception):
    """A well-formed request that's semantically wrong (unknown product_type ambiguity,
    missing required signal, ...) - the route layer turns this into a 422."""


def _resolve_product_type(client_id: int, requested: Optional[str]) -> str:
    """Same "single catalog -> inferred, several -> must say which" convention as the rest
    of the API's Catalog dependency (app.py's resolve_catalog), reimplemented here rather than
    imported: app.py mounts placements.py, so the reverse import would be circular."""
    if requested is not None:
        return requested
    catalogs = db.list_catalogs_for_client(client_id)
    if len(catalogs) > 1:
        raise ValidationError("product_type is required: this account has several catalogs - say which one this placement recommends from")
    return catalogs[0] if catalogs else "default"


def _to_row_fields(payload, *, partial: bool) -> dict[str, Any]:
    """`partial=True` (update): only what the caller actually sent, so an omitted field keeps
    its current stored value. `partial=False` (create): every field, pydantic defaults
    included, so a brand-new row is always fully and consistently populated - the DB column
    defaults exist only as a safety net, not as the source of truth for a create. Nested
    models (signals/filters/business_rules) are dumped to plain dicts automatically by
    model_dump() - JSONB columns store dicts, not pydantic objects."""
    return payload.model_dump(exclude_unset=partial, exclude={"slug"})


def create_placement(client_id: int, payload) -> dict[str, Any]:
    if db.get_placement(client_id, payload.slug) is not None:
        raise ValidationError(f"A placement '{payload.slug}' already exists for this account")
    fields = _to_row_fields(payload, partial=False)
    fields["product_type"] = _resolve_product_type(client_id, fields.get("product_type"))
    return db.create_placement(client_id, payload.slug, **fields)


def _require(client_id: int, slug: str) -> dict[str, Any]:
    row = db.get_placement(client_id, slug)
    if row is None:
        raise NotFoundError(f"No placement '{slug}' for this account")
    return row


def get_placement(client_id: int, slug: str) -> dict[str, Any]:
    return _require(client_id, slug)


def list_placements(client_id: int) -> list[dict[str, Any]]:
    return db.list_placements(client_id)


def update_placement(client_id: int, slug: str, payload) -> dict[str, Any]:
    _require(client_id, slug)
    fields = _to_row_fields(payload, partial=True)
    if "product_type" in fields and fields["product_type"] is not None:
        fields["product_type"] = _resolve_product_type(client_id, fields["product_type"])
    if not fields:
        return _require(client_id, slug)
    return db.update_placement(client_id, slug, **fields)


def set_enabled(client_id: int, slug: str, enabled: bool) -> dict[str, Any]:
    _require(client_id, slug)
    return db.update_placement(client_id, slug, enabled=enabled)


def delete_placement(client_id: int, slug: str) -> None:
    _require(client_id, slug)
    db.delete_placement(client_id, slug)


def get_requirements(client_id: int, slug: str) -> dict[str, Any]:
    return _require(client_id, slug)


def _context_dict(context) -> dict[str, Any]:
    return context.model_dump(exclude_none=True, exclude_defaults=True) if context is not None else {}


def preview_placement(client_id: int, slug: str, context, limit: Optional[int]) -> dict[str, Any]:
    placement = _require(client_id, slug)
    context_dict = _context_dict(context)
    signals_used = {key: bool(context_dict.get(key)) for key in db.PLACEMENT_CONTEXT_SIGNALS}

    missing = engine.missing_required_signals(placement, context_dict)
    if missing:
        return {
            "strategy_used": None, "items": [], "fallback_used": False, "attempted": [],
            "signals_used": signals_used, "warnings": [],
            "errors": [f"Missing required signal(s): {', '.join(missing)}"],
        }

    effective_limit = limit or placement["limit"]
    result = engine.run_placement(placement, context_dict, effective_limit, client_id=client_id)

    from recommender import present_records
    presented = present_records(client_id, placement["product_type"], result.records, legacy=False, include_similar_users=False)
    presented = apply_filters(presented, placement=placement, context=context_dict)[:effective_limit]

    return {
        "strategy_used": result.strategy_used, "items": presented,
        "fallback_used": len(result.attempted) > 1, "attempted": result.attempted,
        "signals_used": signals_used, "warnings": result.warnings, "errors": [],
    }


def run_recommend(client_id: int, slug: str, context, limit: Optional[int]) -> tuple[dict[str, Any], engine.PlacementResult, list[dict], dict[str, Any]]:
    """Returns (placement, engine_result, filtered_presented_items, context_dict) - the route
    layer does the attribution/finalize step (it needs app.py's _finalize_recommendation,
    which placement_service.py can't import without a circular dependency)."""
    placement = db.get_placement(client_id, slug)
    if placement is None or not placement["enabled"]:
        raise NotFoundError(f"No placement '{slug}' for this account")

    context_dict = _context_dict(context)
    missing = engine.missing_required_signals(placement, context_dict)
    if missing:
        raise ValidationError(f"Missing required signal(s): {', '.join(missing)}")

    effective_limit = limit or placement["limit"]
    result = engine.run_placement(placement, context_dict, effective_limit, client_id=client_id)

    from recommender import present_records
    presented = present_records(client_id, placement["product_type"], result.records, legacy=False, include_similar_users=False)
    presented = apply_filters(presented, placement=placement, context=context_dict)[:effective_limit]
    return placement, result, presented, context_dict


# ---------------------------------------------------------------------------
# Integration validator - get_placement_health / get_tracking_requirements /
# get_recent_integration_events (MCP tools built on these).
# ---------------------------------------------------------------------------

# Fixed severity policy for "this check found zero events" - not derived from the placement's
# own config, since these are about whether the *site's own instrumentation* is wired up, not
# whether the placement is configured correctly (that's what missing_required_signals covers).
# purchase is the one funnel event worth flagging as a hard problem when absent; add_to_cart is
# a real gap but plenty of catalogs (subscriptions, content, non-cart flows) legitimately have
# none - matches the brief's own example (⚠ add_to_cart, ✗ purchases).
_ZERO_SEVERITY = {
    "recommendations_requested": "missing",
    "impressions_received": "warning",
    "recommendation_clicks_received": "warning",
    "product_views_received": "warning",
    "add_to_cart_received": "warning",
    "purchases_received": "missing",
}
_HEALTH_ORDER = ["missing", "warning", "ok"]  # for computing `overall` - worst check wins

_PRODUCT_VIEW = {"event_type": "product_view", "why": "feeds session-based recommendations (recently viewed) for every placement, not just this one"}
_ADD_TO_CART = {"event_type": "add_to_cart", "why": "a mid-funnel signal (weight: moyen) - optional but improves ranking"}
_PURCHASE = {"event_type": "purchase", "why": "the strongest training signal (weight: fort) and what attribution ultimately measures"}
_DEFAULT_CATALOG_EVENTS = [_PRODUCT_VIEW, _ADD_TO_CART, _PURCHASE]  # fallback: custom/unrecognized context_type

# Which catalog-wide events actually make sense to expect, by context_type - a cart visitor is
# already past "viewing" and about to add/buy, so product_view isn't the useful signal there; a
# content page's visitor may never touch a cart at all, so add_to_cart/purchase would sit
# permanently "missing" for a placement that was never going to see them. Every context_type
# not listed here (today: only "custom") falls back to the full funnel.
_DEFAULT_EVENTS_BY_CONTEXT: dict[str, list[dict[str, str]]] = {
    "product_page": [_PRODUCT_VIEW, _ADD_TO_CART, _PURCHASE],
    "listing_page": [_PRODUCT_VIEW, _ADD_TO_CART, _PURCHASE],
    "category_page": [_PRODUCT_VIEW, _ADD_TO_CART, _PURCHASE],
    "homepage": [_PRODUCT_VIEW, _ADD_TO_CART, _PURCHASE],
    "cart": [_ADD_TO_CART, _PURCHASE],
    "account": [_PRODUCT_VIEW, _PURCHASE],
    "content_page": [_PRODUCT_VIEW],
}

# Exact check names for the default trio, kept stable for every placement that never set
# tracking_configuration (i.e. every placement created before this existed) - a custom event
# type instead gets a generic "<type>_received" name.
_CHECK_NAME_OVERRIDES = {"product_view": "product_views_received", "purchase": "purchases_received"}


def _catalog_tracked_events(placement: dict[str, Any]) -> list[dict[str, str]]:
    """The catalog-wide (not placement-specific) events this placement's tracking-requirements
    and health check report on: whatever the tenant explicitly attached via
    tracking_configuration.event_types (the Événements page) takes priority - or, unset, a
    default that depends on where the placement actually sits (see
    _DEFAULT_EVENTS_BY_CONTEXT), not a single fixed trio for every placement regardless of
    context."""
    configured = (placement.get("tracking_configuration") or {}).get("event_types")
    if configured:
        return [{"event_type": t, "why": "Configuré pour ce placement (page Événements)."} for t in configured]
    return _DEFAULT_EVENTS_BY_CONTEXT.get(placement.get("context_type"), _DEFAULT_CATALOG_EVENTS)


def _check(name: str, count: int, *, required: Optional[bool] = None) -> dict[str, Any]:
    """`required=None` (the fixed instrumentation checks): severity of a zero count comes from
    _ZERO_SEVERITY. `required=True/False` (the signal-relevance checks): severity follows
    whether *this placement* actually requires that signal."""
    if count > 0:
        return {"name": name, "status": "ok", "count": count, "message": f"{count} received in the window"}
    status = _ZERO_SEVERITY.get(name, "warning") if required is None else ("missing" if required else "warning")
    return {"name": name, "status": status, "count": 0, "message": "not received in the window"}


def get_health(client_id: int, slug: str, window_hours: int = 24) -> dict[str, Any]:
    placement = _require(client_id, slug)
    since = db.utcnow() - timedelta(hours=window_hours)
    signals = placement.get("signals") or {}
    required_signals = set(signals.get("required") or [])
    relevant_signals = required_signals | set(signals.get("optional") or [])

    rec_stats = db.count_recent_recommendation_calls(client_id, slug, since)
    checks = [_check("recommendations_requested", rec_stats["total"])]

    if {"current_item_id"} & relevant_signals:
        checks.append(_check("current_item_supplied", rec_stats["with_item"], required="current_item_id" in required_signals))
    if {"session_id", "anonymous_id"} & relevant_signals:
        checks.append(_check("anonymous_session_supplied", rec_stats["with_session"], required=bool({"session_id", "anonymous_id"} & required_signals)))

    checks.append(_check("impressions_received", db.count_recent_interactions_by_type(client_id, "impression", since, placement=slug)))
    checks.append(_check("recommendation_clicks_received", db.count_recent_interactions_by_type(client_id, "click", since, placement=slug)))

    for event in _catalog_tracked_events(placement):
        event_type = event["event_type"]
        check_name = _CHECK_NAME_OVERRIDES.get(event_type, f"{event_type}_received")
        internal_type = db.EVENT_TYPE_ALIASES.get(event_type, event_type)
        checks.append(_check(check_name, db.count_recent_interactions_by_type(client_id, internal_type, since)))

    overall = "ok"
    for status in (c["status"] for c in checks):
        if _HEALTH_ORDER.index(status) < _HEALTH_ORDER.index(overall):
            overall = status
    return {"slug": slug, "window_hours": window_hours, "overall": overall, "checks": checks}


def get_tracking_requirements(client_id: int, slug: str) -> dict[str, Any]:
    placement = _require(client_id, slug)
    required_events = [
        {"event_type": "recommendation_impression", "scope": "placement", "why": "confirms recommendations are actually being rendered, not just requested"},
        {"event_type": "recommendation_click", "scope": "placement", "why": "the primary signal that a recommendation is relevant - drives collaborative/session quality over time"},
    ] + [
        {"event_type": event["event_type"], "scope": "catalog", "why": event["why"]}
        for event in _catalog_tracked_events(placement)
    ]
    return {"slug": slug, "required_context": placement["signals"], "required_events": required_events}


def get_recent_events(client_id: int, slug: str, window_hours: int = 24, limit: int = 20) -> dict[str, Any]:
    _require(client_id, slug)
    since = db.utcnow() - timedelta(hours=window_hours)
    calls = db.list_recent_recommendation_calls(client_id, slug, since, limit)
    interactions = db.list_recent_interactions(client_id, ["impression", "click"], since, limit, placement=slug)
    return {"slug": slug, "window_hours": window_hours, "recommendation_calls": calls, "interactions": interactions}
