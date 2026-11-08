"""GET /analytics/* - recommendation performance and plan usage for an integration that holds
the secret key (or a developer key with the `analytics:read` scope), e.g. the WordPress plugin's
dashboard. Same numbers as the console's /clients/me/analytics/* (Supabase session), same plan
rules: the requested window is clamped to the plan (Free: 7 days, Pro: 90), the per-placement
breakdown is available on every plan, the trend/revenue detail and the top items are Pro only.

Never the public key (scoped_auth.require_scope refuses it with a 403): these are business
numbers, not something a web page should be able to read.

Also home to the two helpers app.py's console routes share with this router
(resolve_analytics_window, usage_summary) - app.py mounts this router, so they live here rather
than there to avoid a circular import.
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: E402

import db  # noqa: E402
from schemas import (  # noqa: E402
    AnalyticsItemsOut, AnalyticsKeyDetailOut, AnalyticsKeySummaryOut, AnalyticsPlacementsOut,
    AnalyticsPlanOut, ErrorResponse,
)
from scoped_auth import require_scope  # noqa: E402

router = APIRouter(prefix="/analytics", tags=["analytics"])

SCOPE = "analytics:read"


def resolve_analytics_window(plan: str, start: Optional[date], end: Optional[date], default_days: int) -> tuple[datetime, datetime, date, date]:
    """Turns a caller-chosen [start, end] (either may be omitted) into a concrete UTC datetime
    range, clamped to this plan's analytics_window_days cap - never trust the client's
    requested range beyond what the plan allows, same principle as every other limit in this
    API. Returns (since, until, start_date, end_date); the last two are what the response
    echoes back so the caller knows what was actually used."""
    max_days = db.get_plan_limits(plan)["analytics_window_days"]
    end_date = end or datetime.now(timezone.utc).date()
    start_date = start or (end_date - timedelta(days=default_days - 1))
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    if max_days is not None:
        earliest = end_date - timedelta(days=max_days - 1)
        if start_date < earliest:
            start_date = earliest
    since = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    until = datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)
    return since, until, start_date, end_date


def usage_summary(client_id: int) -> dict:
    plan = db.get_client_plan(client_id)
    limits = db.get_plan_limits(plan)
    return {
        "plan": plan,
        "product_count": db.count_products_for_client(client_id),
        "product_limit": limits["product_limit"],
        "index_count": len(db.list_product_types_for_client(client_id)),
        "index_limit": limits["index_limit"],
        "data_source_count": db.count_data_sources(client_id),
        "data_source_limit": limits["data_source_limit"],
    }


def _errors(*codes: int) -> dict:
    docs = {
        401: "Missing or invalid X-API-Key.",
        403: "The key can't read analytics (the public key, or a developer key without `analytics:read`), or this plan doesn't include this data.",
        422: "A query parameter is malformed (dates are YYYY-MM-DD).",
    }
    return {code: {"model": ErrorResponse, "description": docs[code]} for code in codes}


START = Query(None, description="First day included (YYYY-MM-DD, UTC). Defaults to the window's default length before `end`; moved forward when the span exceeds the plan's cap.")
END = Query(None, description="Last day included (YYYY-MM-DD, UTC). Defaults to today.")


def _window(client_id: int, start: Optional[date], end: Optional[date], default_days: int) -> tuple[datetime, datetime, dict]:
    plan = db.get_client_plan(client_id)
    since, until, start_date, end_date = resolve_analytics_window(plan, start, end, default_days)
    echo = {
        "start": start_date.isoformat(), "end": end_date.isoformat(), "plan": plan,
        "window_days": (end_date - start_date).days + 1,
        "max_window_days": db.get_plan_limits(plan)["analytics_window_days"],
    }
    return since, until, echo


def _require_detail(client_id: int, what: str) -> None:
    plan = db.get_client_plan(client_id)
    if not db.get_plan_limits(plan)["analytics_detail"]:
        raise HTTPException(status_code=403, detail=db.plan_limit_message(plan, what))


@router.get("/summary", response_model=AnalyticsKeySummaryOut, summary="Recommendation performance - totals", responses=_errors(401, 403, 422))
def analytics_summary(start: Optional[date] = START, end: Optional[date] = END, client_id: int = Depends(require_scope(SCOPE))):
    """Recommendations served, impressions, clicks and CTR over the window - every plan. The
    window defaults to the last 7 days and is clamped to the plan (Free: 7 days, Pro: 90);
    `start`, `end`, `window_days` and `plan` in the answer say what was actually used."""
    since, until, echo = _window(client_id, start, end, default_days=7)
    return {**echo, **db.recommendation_performance_summary(client_id, since, until=until)}


@router.get("/placements", response_model=AnalyticsPlacementsOut, summary="Recommendation performance - by placement", responses=_errors(401, 403, 422))
def analytics_placements(start: Optional[date] = START, end: Optional[date] = END, client_id: int = Depends(require_scope(SCOPE))):
    """One row per placement (recommendation block) with activity in the window:
    recommendations served, impressions, clicks, CTR, and the purchases and revenue attributed
    to it - every plan. A purchase is attributed when it carries a `recommendation_id`; it is
    credited to the placement that recommendation was served for, or to the purchase's own
    `placement`. Revenue is `properties.price` x `quantity`. Window: last 7 days by default,
    clamped to the plan."""
    since, until, echo = _window(client_id, start, end, default_days=7)
    return {**echo, "placements": db.recommendation_performance_by_placement_with_revenue(client_id, since, until)}


@router.get("/detail", response_model=AnalyticsKeyDetailOut, summary="Recommendation performance - daily trend and revenue", responses=_errors(401, 403, 422))
def analytics_detail(start: Optional[date] = START, end: Optional[date] = END, client_id: int = Depends(require_scope(SCOPE))):
    """A day-by-day trend (recommendations served, impressions, clicks, CTR) and the total
    revenue attributed to recommendations (purchases carrying a `recommendation_id`). Pro plan
    and above: 403 on Free. Window: last 30 days by default, clamped to the plan."""
    _require_detail(client_id, "detailed performance data (trend, by placement, revenue)")
    since, until, echo = _window(client_id, start, end, default_days=30)
    return {
        **echo,
        "timeseries": db.recommendation_performance_timeseries(client_id, since, until=until),
        "revenue": db.recommendation_attributed_revenue(client_id, since, until=until),
    }


@router.get("/items", response_model=AnalyticsItemsOut, summary="Top recommended items", responses=_errors(401, 403, 422))
def analytics_items(
    start: Optional[date] = START, end: Optional[date] = END,
    limit: int = Query(10, ge=1, le=50, description="How many items to return (1-50)."),
    client_id: int = Depends(require_scope(SCOPE)),
):
    """The items recommendations worked best on: per item, how many recommendation responses
    included it, and the impressions, clicks, purchases and revenue of events carrying a
    `recommendation_id` - ordered by clicks, then purchases. `title` comes from the catalog.
    Pro plan and above: 403 on Free. Window: last 30 days by default, clamped to the plan."""
    _require_detail(client_id, "top recommended items")
    since, until, echo = _window(client_id, start, end, default_days=30)
    return {**echo, "items": db.recommendation_top_items(client_id, since, until, limit=limit)}


@router.get("/plan", response_model=AnalyticsPlanOut, summary="My plan, its limits and usage", responses=_errors(401, 403))
def analytics_plan(client_id: int = Depends(require_scope(SCOPE))):
    """The account's plan, everything it allows, and the current usage next to each capped
    quantity (products, catalogs, data sources) - enough to show e.g. "20 / 50 products, Free"
    and which analytics this plan includes (`limits.analytics_window_days`,
    `limits.analytics_detail`). Every plan."""
    plan = db.get_client_plan(client_id)
    return {
        "plan": plan, "plan_name": db.plan_display_name(plan),
        "limits": {"plan": plan, **db.get_plan_limits(plan)},
        "usage": usage_summary(client_id),
        "upgrade_url": db.UPGRADE_URL,
    }
