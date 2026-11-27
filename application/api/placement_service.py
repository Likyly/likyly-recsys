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
import rec_cache  # noqa: E402
from placement_filters import apply_filters, apply_variant_rules, group_of  # noqa: E402


class NotFoundError(Exception):
    """No such placement for this account - the route layer turns this into a 404,
    deliberately indistinguishable from "never existed" (see db.py's placement functions,
    all client_id-scoped)."""


class ValidationError(Exception):
    """A well-formed request that's semantically wrong (unknown product_type ambiguity,
    missing required signal, ...) - the route layer turns this into a 422."""


def _known_catalogs(client_id: int) -> list[str]:
    """The catalogs a placement can recommend from: those holding items or events, plus those
    a data source writes into (connected, first sync not done yet)."""
    sources = {s["product_type"] for s in db.list_data_sources(client_id) if s.get("product_type")}
    return sorted(set(db.list_catalogs_for_client(client_id)) | sources)


def _resolve_product_type(client_id: int, requested: Optional[str]) -> str:
    """Same "single catalog -> inferred, several -> must say which" convention as the rest
    of the API's Catalog dependency (app.py's resolve_catalog), reimplemented here rather than
    imported: app.py mounts placements.py, so the reverse import would be circular. A catalog
    the account doesn't have is refused, naming those it has: a placement on a deleted or
    misspelled catalog would only ever answer with nothing."""
    catalogs = _known_catalogs(client_id)
    if requested is not None:
        if catalogs and requested not in catalogs:
            names = ", ".join(f"'{name}'" for name in catalogs)
            raise ValidationError(f"No catalog '{requested}' in this account - its catalogs: {names}. Set product_type to one of them.")
        return requested
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
    rec_cache.purge(client_id, slug)
    return db.update_placement(client_id, slug, **fields)


def set_enabled(client_id: int, slug: str, enabled: bool) -> dict[str, Any]:
    _require(client_id, slug)
    return db.update_placement(client_id, slug, enabled=enabled)


def delete_placement(client_id: int, slug: str) -> None:
    _require(client_id, slug)
    rec_cache.purge(client_id, slug)
    db.delete_placement(client_id, slug)


def get_requirements(client_id: int, slug: str) -> dict[str, Any]:
    return _require(client_id, slug)


def _context_dict(context) -> dict[str, Any]:
    return context.model_dump(exclude_none=True, exclude_defaults=True) if context is not None else {}


# The placement's filters (in stock, never the current item, categories...) run after the
# strategy has ranked its candidates, so asking the engine for exactly `limit` items came back
# short whenever one of them was filtered out. Ask for more, then top up with popular items.
_CANDIDATE_MARGIN = 3


def _purchased(client_id: int, placement: dict[str, Any], context_dict: dict[str, Any]) -> set[int]:
    user_id = context_dict.get("user_id")
    if not user_id or not (placement.get("business_rules") or {}).get("exclude_purchased_items", True):
        return set()
    product_type = placement["product_type"]
    internal = db.resolve_internal_ids(client_id, product_type, db.KIND_USER, [user_id]).get(user_id)
    return db.get_purchased_work_ids(client_id, product_type, internal) if internal is not None else set()


def _variant_rules(client_id: int, placement: dict[str, Any], context_dict: dict[str, Any], bought: set[int]):
    """The one_per_group / closest_color step bound to this request, or None when the
    placement doesn't ask for it: it needs the current item and each model's other items."""
    from recommender import present_records

    rules = placement.get("business_rules") or {}
    if not rules.get("one_per_group"):
        return None
    product_type = placement["product_type"]
    group_by = rules.get("group_by") or "group_id"
    def items_of(external_ids: list[str]) -> list[dict]:
        internal = db.resolve_internal_ids(client_id, product_type, db.KIND_ITEM, external_ids)
        records = db.fetch_product_records(client_id, product_type, [w for w in internal.values() if w is not None])
        return present_records(client_id, product_type, records, legacy=False, include_similar_users=False)

    current_id = context_dict.get("current_item_id")
    current = next(iter(items_of([current_id])), None) if current_id else None
    # The cart's models are left out too: never another colour of a bag already in the cart.
    cart = items_of([str(i) for i in context_dict.get("cart_item_ids") or []][:50])

    def variants_for(groups: list[str]) -> dict[str, list[dict]]:
        records = [r for r in db.fetch_group_records(client_id, product_type, group_by, groups) if int(r["work_id"]) not in bought]
        items = apply_filters(
            present_records(client_id, product_type, records, legacy=False, include_similar_users=False),
            placement=placement, context=context_dict,
        )
        by_group: dict[str, list[dict]] = {}
        for item in items:
            group = group_of(item, group_by)
            if group is not None:
                by_group.setdefault(group, []).append(item)
        return by_group

    return lambda items: apply_variant_rules(items, placement=placement, current=current, variants_for=variants_for, cart=cart)


def _pinned(client_id: int, placement: dict[str, Any], context_dict: dict[str, Any], bought: set[int]) -> list[dict]:
    """The merchant's products put forward (business_rules.pinned_item_ids), in their order,
    through the same filters and rules as the rest: out of stock, the current item, the cart and
    what the visitor bought stay out."""
    from recommender import present_records

    ids = [str(i) for i in (placement.get("business_rules") or {}).get("pinned_item_ids") or []][:12]
    if not ids:
        return []
    product_type = placement["product_type"]
    internal = db.resolve_internal_ids(client_id, product_type, db.KIND_ITEM, ids)
    records = [r for r in db.fetch_product_records(client_id, product_type, [w for w in internal.values() if w is not None]) if int(r["work_id"]) not in bought]
    items = apply_filters(present_records(client_id, product_type, records, legacy=False, include_similar_users=False), placement=placement, context=context_dict)
    by_id = {item["item_id"]: item for item in items}
    pinned = []
    for item_id in ids:
        if item_id in by_id:
            pinned.append({**by_id[item_id], "pinned": True, "explanation": {"reason": "Mis en avant par la boutique"}})
    return pinned


def _rank_and_fill(client_id: int, placement: dict[str, Any], context_dict: dict[str, Any], limit: int, *, blend_session: bool) -> tuple[engine.PlacementResult, list[dict]]:
    from recommender import present_records, rec_popular

    product_type = placement["product_type"]
    if engine.current_item_unknown(client_id, placement, context_dict):
        return engine.PlacementResult("none", [], [], [
            f"current_item_id '{context_dict['current_item_id']}' is not in catalog '{product_type}' - nothing shown "
            "(business_rules.unknown_current_item 'popular' shows the most popular items instead)"
        ]), []
    want = min(100, limit * _CANDIDATE_MARGIN)
    result = engine.run_placement(placement, context_dict, want, client_id=client_id, blend_session=blend_session)
    # A signed-in visitor is never offered back what they already bought (business rule
    # exclude_purchased_items, on by default): content and hybrid rank by similarity and would
    # otherwise put her own purchases first.
    bought = _purchased(client_id, placement, context_dict)
    if bought:
        result.records = [r for r in result.records if r.get("work_id") is None or int(r["work_id"]) not in bought]
    candidates = apply_filters(
        present_records(client_id, product_type, result.records, legacy=False, include_similar_users=False),
        placement=placement, context=context_dict,
    )
    # One item per model / its closest colour (business_rules.one_per_group, closest_color).
    variants = _variant_rules(client_id, placement, context_dict, bought)
    if variants:
        candidates = variants(candidates)
    # The merchant's products put forward come first; LIKYLY's choice fills the rest.
    pinned = _pinned(client_id, placement, context_dict, bought)
    if pinned:
        pinned_ids = {item["item_id"] for item in pinned}
        candidates = pinned + [item for item in candidates if item["item_id"] not in pinned_ids]
    presented = candidates[:limit]

    if len(presented) < limit and result.strategy_used != "popular":
        seen = {item["item_id"] for item in presented}
        exclude = {int(r["work_id"]) for r in result.records if r.get("work_id") is not None} | bought
        extra = rec_popular(client_id, product_type, want, exclude_work_ids=exclude, catalog_fallback=True)
        extra = apply_filters(
            present_records(client_id, product_type, extra, legacy=False, include_similar_users=False),
            placement=placement, context=context_dict,
        )
        if variants:
            # Topped up items are models too: not one already shown, nor the current one.
            extra = variants(presented + [item for item in extra if item["item_id"] not in seen])
        added = [item for item in extra if item["item_id"] not in seen][: limit - len(presented)]
        if added:
            presented += added
            result.warnings.append(f"'{result.strategy_used}' had {limit - len(added)} item(s) after filters - topped up with {len(added)} popular item(s)")
    return result, presented


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
    # Same answer as a real call: the Pro session mix included.
    result, presented = _rank_and_fill(
        client_id, placement, context_dict, effective_limit,
        blend_session=db.get_client_plan(client_id) in SESSION_BLEND_PLANS,
    )

    return {
        "strategy_used": result.strategy_used, "items": presented,
        "fallback_used": len(result.attempted) > 1, "attempted": result.attempted,
        "signals_used": signals_used, "warnings": result.warnings, "errors": [],
    }


# Plans whose anonymous visitors get recommendations mixing the current product with their
# session (recommender.blend_content_session); Free gets the current product's neighbours only.
SESSION_BLEND_PLANS = {db.PLAN_PRO, db.PLAN_UNLIMITED}


def _session_has_history(client_id: int, product_type: str, context: dict[str, Any]) -> bool:
    session = context.get("session_id") or context.get("anonymous_id")
    return bool(session) and bool(db.get_recent_viewed_work_ids_for_session(client_id, product_type, session, limit=1))


def run_recommend(client_id: int, slug: str, context, limit: Optional[int]) -> tuple[dict[str, Any], engine.PlacementResult, list[dict], dict[str, Any], str]:
    """Returns (placement, engine_result, filtered_presented_items, context_dict, cache) - the
    route layer does the attribution/finalize step (it needs app.py's _finalize_recommendation,
    which placement_service.py can't import without a circular dependency). `cache` is "HIT",
    "MISS" or "BYPASS" (a signed-in visitor: see rec_cache.py)."""
    placement = db.get_placement(client_id, slug)
    if placement is None or not placement["enabled"]:
        raise NotFoundError(f"No placement '{slug}' for this account")

    context_dict = _context_dict(context)
    missing = engine.missing_required_signals(placement, context_dict)
    if missing:
        raise ValidationError(f"Missing required signal(s): {', '.join(missing)}")

    effective_limit = limit or placement["limit"]
    # Pro feature: anonymous recommendations that follow the visitor's session.
    blend_session = db.get_client_plan(client_id) in SESSION_BLEND_PLANS
    cacheable = rec_cache.is_cacheable_request(context_dict, follows_session=blend_session)
    # With no current product (a home or listing block), a visitor who has already looked at
    # products gets what her session points to: that answer is hers, never the shared one.
    if cacheable and not context_dict.get("current_item_id") and _session_has_history(client_id, placement["product_type"], context_dict):
        cacheable = False
    cache_key = rec_cache.key(client_id, {**placement, "slug": slug}, context_dict, effective_limit) if cacheable else None
    if cache_key is not None:
        cached = rec_cache.get(cache_key)
        if cached is not None:
            result, presented = cached
            return placement, result, [dict(item) for item in presented], context_dict, "HIT"

    result, presented = _rank_and_fill(client_id, placement, context_dict, effective_limit, blend_session=blend_session)
    if cache_key is not None and result.strategy_used in rec_cache.CACHEABLE_STRATEGIES:
        rec_cache.put(cache_key, (result, [dict(item) for item in presented]))
    return placement, result, presented, context_dict, ("MISS" if cacheable else "BYPASS")


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
_ADD_TO_CART = {"event_type": "add_to_cart", "why": "a mid-funnel signal (medium weight) - optional but improves ranking"}
_PURCHASE = {"event_type": "purchase", "why": "the strongest training signal (strong weight) and what attribution ultimately measures"}
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


def _catalog_tracked_events(placement: dict[str, Any], language: str = "fr") -> list[dict[str, str]]:
    """The catalog-wide (not placement-specific) events this placement's tracking-requirements
    and health check report on: whatever the tenant explicitly attached via
    tracking_configuration.event_types (the Événements page) takes priority - or, unset, a
    default that depends on where the placement actually sits (see
    _DEFAULT_EVENTS_BY_CONTEXT), not a single fixed trio for every placement regardless of
    context."""
    configured = (placement.get("tracking_configuration") or {}).get("event_types")
    if configured:
        why = "Configured for this placement (Events page)." if language == "en" else "Configuré pour ce placement (page Événements)."
        return [{"event_type": t, "why": why} for t in configured]
    return _DEFAULT_EVENTS_BY_CONTEXT.get(placement.get("context_type"), _DEFAULT_CATALOG_EVENTS)


def _check(name: str, count: int, *, required: Optional[bool] = None, language: str = "en") -> dict[str, Any]:
    """`required=None` (the fixed instrumentation checks): severity of a zero count comes from
    _ZERO_SEVERITY. `required=True/False` (the signal-relevance checks): severity follows
    whether *this placement* actually requires that signal."""
    if count > 0:
        message = f"{count} reçu{'s' if count > 1 else ''} sur la période" if language == "fr" else f"{count} received in the window"
        return {"name": name, "status": "ok", "count": count, "message": message}
    status = _ZERO_SEVERITY.get(name, "warning") if required is None else ("missing" if required else "warning")
    return {"name": name, "status": status, "count": 0, "message": "rien reçu sur la période" if language == "fr" else "not received in the window"}


def _public_key_rotated_check(client_id: int, last_call_at, language: str = "en") -> Optional[dict[str, Any]]:
    """Calls came in, then a public key was regenerated (the workspace's) or revoked (a site's)
    and none came since: the site still
    sends the old key, every call (recommend and events) gets a 401 that no workspace can be
    credited with, so the counts above keep looking fine for the rest of the window while the
    block is gone from the site. Only reported in that case - a placement whose calls carry on
    after a rotation (new key in place, or a server calling with the secret key) is fine."""
    if last_call_at is None:
        return None
    disabled_at = db.latest_public_key_disabled_at(client_id)
    if disabled_at is None or disabled_at <= last_call_at:
        return None
    return {
        "name": "public_key_rotated",
        "status": "missing",
        "count": 0,
        "message": (
            f"une clé publique a été régénérée ou révoquée le {disabled_at:%d/%m/%Y à %H:%M} UTC, aucun appel de recommandation depuis "
            f"(le dernier le {last_call_at:%d/%m/%Y à %H:%M} UTC) : un site qui utilise encore cette clé reçoit une erreur 401 - donnez-lui une clé valide"
            if language == "fr" else
            f"a public key was regenerated or revoked at {disabled_at:%Y-%m-%d %H:%M} UTC, no recommendation call since "
            f"(last one at {last_call_at:%Y-%m-%d %H:%M} UTC): a site still using that key gets 401 - give it a working key"
        ),
    }


def get_health(client_id: int, slug: str, window_hours: int = 24, language: str = "en") -> dict[str, Any]:
    """`language` ("en" | "fr"): the checks' `message` (the console asks for its page's language)."""
    placement = _require(client_id, slug)
    lang = {"language": language}
    since = db.utcnow() - timedelta(hours=window_hours)
    signals = placement.get("signals") or {}
    required_signals = set(signals.get("required") or [])
    relevant_signals = required_signals | set(signals.get("optional") or [])

    rec_stats = db.count_recent_recommendation_calls(client_id, slug, since)
    checks = [_check("recommendations_requested", rec_stats["total"], **lang)]
    rotated = _public_key_rotated_check(client_id, rec_stats["last_at"], language)
    if rotated:
        checks.append(rotated)

    if {"current_item_id"} & relevant_signals:
        checks.append(_check("current_item_supplied", rec_stats["with_item"], required="current_item_id" in required_signals, **lang))
    if {"session_id", "anonymous_id"} & relevant_signals:
        checks.append(_check("anonymous_session_supplied", rec_stats["with_session"], required=bool({"session_id", "anonymous_id"} & required_signals), **lang))

    checks.append(_check("impressions_received", db.count_recent_interactions_by_type(client_id, "impression", since, placement=slug), **lang))
    checks.append(_check("recommendation_clicks_received", db.count_recent_interactions_by_type(client_id, "click", since, placement=slug), **lang))

    for event in _catalog_tracked_events(placement):
        event_type = event["event_type"]
        check_name = _CHECK_NAME_OVERRIDES.get(event_type, f"{event_type}_received")
        internal_type = db.EVENT_TYPE_ALIASES.get(event_type, event_type)
        checks.append(_check(check_name, db.count_recent_interactions_by_type(client_id, internal_type, since), **lang))

    overall = "ok"
    for status in (c["status"] for c in checks):
        if _HEALTH_ORDER.index(status) < _HEALTH_ORDER.index(overall):
            overall = status
    return {"slug": slug, "window_hours": window_hours, "overall": overall, "checks": checks}


def get_tracking_requirements(client_id: int, slug: str, language: str = "fr") -> dict[str, Any]:
    placement = _require(client_id, slug)
    required_events = [
        {"event_type": "recommendation_impression", "scope": "placement", "why": "confirms recommendations are actually being rendered, not just requested"},
        {"event_type": "recommendation_click", "scope": "placement", "why": "the primary signal that a recommendation is relevant - drives collaborative/session quality over time"},
    ] + [
        {"event_type": event["event_type"], "scope": "catalog", "why": event["why"]}
        for event in _catalog_tracked_events(placement, language)
    ]
    return {"slug": slug, "required_context": placement["signals"], "required_events": required_events}


def get_recent_events(client_id: int, slug: str, window_hours: int = 24, limit: int = 20) -> dict[str, Any]:
    _require(client_id, slug)
    since = db.utcnow() - timedelta(hours=window_hours)
    calls = db.list_recent_recommendation_calls(client_id, slug, since, limit)
    interactions = db.list_recent_interactions(client_id, ["impression", "click"], since, limit, placement=slug)
    return {"slug": slug, "window_hours": window_hours, "recommendation_calls": calls, "interactions": interactions}


def purge_cache(client_id: int, slug: Optional[str] = None) -> dict[str, Any]:
    """Empty the anonymous recommendation cache: one placement's (checked to exist) or all."""
    if slug is not None:
        _require(client_id, slug)
    purged = rec_cache.purge(client_id, slug)
    what = f"placement '{slug}'" if slug else "every placement"
    return {"message": f"Recommendation cache emptied for {what}: the next calls are computed afresh.", "purged": purged}
