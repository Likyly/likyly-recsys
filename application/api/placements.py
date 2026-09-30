"""Routes for Placements - the recommended abstraction for embedding LIKYLY recommendations
(see docs/placements.md). Two auth tiers on one router:
  * admin (CRUD, preview, requirements) - secret key or a developer key with
    placements:read/placements:write (scoped_auth.require_scope).
  * POST /placements/{slug}/recommend - the runtime endpoint, safe with the public key
    (scoped_auth.public_ok_client_id), sitting alongside /getRec for real site traffic.

Business logic lives in placement_service.py; this stays thin (auth, params, HTTP status).
_finalize_recommendation/present_records/Catalog live in app.py and are imported lazily
inside the one route that needs them (recommend), to avoid a circular import - app.py mounts
this router, so a module-level import the other way round isn't possible.
"""
import os
import sys
from typing import List

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response

import placement_service as service  # noqa: E402
from schemas import (  # noqa: E402
    ErrorResponse, Message, Placement, PlacementCreate, PlacementHealthOut, PlacementPreviewOut,
    PlacementPreviewRequest, PlacementRecommendRequest, PlacementRecommendResponse,
    PlacementRequirementsOut, PlacementUpdate, RecentIntegrationEventsOut, TrackingRequirementsOut,
)
from scoped_auth import public_ok_client_id, require_scope  # noqa: E402

router = APIRouter(prefix="/placements", tags=["placements"])


def _errors(*codes: int) -> dict:
    docs = {
        401: "Missing or invalid X-API-Key.",
        403: "The key presented doesn't carry the scope this endpoint requires.",
        404: "No such placement for this account (or it is disabled, for /recommend).",
        422: "The request is malformed, or a signal the placement requires is missing.",
    }
    return {code: {"model": ErrorResponse, "description": docs[code]} for code in codes}


def _not_found(error: service.NotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


def _invalid(error: service.ValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@router.post("", response_model=Placement, status_code=201, summary="Create a placement", responses=_errors(401, 403, 422))
def create_placement(payload: PlacementCreate, client_id: int = Depends(require_scope("placements:write"))):
    """Registers a named recommendation configuration - "what to recommend, where, with what
    constraints" - referenced afterwards by `slug` from POST /placements/{slug}/recommend. Call
    preview_placement next to check it against a sample context before wiring up a site."""
    try:
        return service.create_placement(client_id, payload)
    except service.ValidationError as error:
        raise _invalid(error)


@router.get("", response_model=List[Placement], summary="List placements", responses=_errors(401, 403))
def list_placements(client_id: int = Depends(require_scope("placements:read"))):
    """Every placement configured for this account, whatever the strategy or status."""
    return service.list_placements(client_id)


@router.get("/{slug}", response_model=Placement, summary="Get a placement", responses=_errors(401, 403, 404, 422))
def get_placement(slug: str, client_id: int = Depends(require_scope("placements:read"))):
    """One placement's full configuration by slug."""
    try:
        return service.get_placement(client_id, slug)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.patch("/{slug}", response_model=Placement, summary="Update a placement", responses=_errors(401, 403, 404, 422))
def update_placement(slug: str, payload: PlacementUpdate, client_id: int = Depends(require_scope("placements:write"))):
    """Partial update - only the fields given are changed. Sending `signals`/`filters`/
    `business_rules` at all replaces that whole sub-object (not a deep merge). Bumps
    `version`, whatever changed."""
    try:
        return service.update_placement(client_id, slug, payload)
    except service.NotFoundError as error:
        raise _not_found(error)
    except service.ValidationError as error:
        raise _invalid(error)


@router.post("/{slug}/deactivate", response_model=Placement, summary="Disable a placement (reversible)", responses=_errors(401, 403, 404, 422))
def deactivate_placement(slug: str, client_id: int = Depends(require_scope("placements:write"))):
    """The safe default of `delete_or_disable_placement`: POST /placements/{slug}/recommend
    starts 404ing immediately, nothing is deleted - re-enable with PATCH {"enabled": true}."""
    try:
        return service.set_enabled(client_id, slug, False)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.delete("/{slug}", response_model=Message, summary="Delete a placement permanently", responses=_errors(401, 403, 404, 422))
def delete_placement(slug: str, client_id: int = Depends(require_scope("placements:write"))):
    """Permanent - prefer deactivate_placement unless the configuration itself is wrong and
    should stop existing, not just stop serving."""
    try:
        service.delete_placement(client_id, slug)
    except service.NotFoundError as error:
        raise _not_found(error)
    return {"message": f"Placement '{slug}' deleted"}


@router.get("/{slug}/requirements", response_model=PlacementRequirementsOut, summary="What this placement needs to run", responses=_errors(401, 403, 404, 422))
def get_placement_requirements(slug: str, client_id: int = Depends(require_scope("placements:read"))):
    """Static introspection (no engine call, no context needed) - required/optional signals,
    strategy/fallback, context_type. Call this before wiring a site to a placement, to know
    exactly what context to send at /recommend time."""
    try:
        return service.get_requirements(client_id, slug)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.get("/{slug}/tracking-requirements", response_model=TrackingRequirementsOut, summary="Which events this placement needs instrumented", responses=_errors(401, 403, 404, 422))
def get_tracking_requirements(slug: str, client_id: int = Depends(require_scope("placements:read"))):
    """Static (no query) - the `get_tracking_requirements` MCP tool. Which events to send and
    why (`recommendation_impression`/`recommendation_click` are placement-scoped;
    `product_view`/`add_to_cart`/`purchase` are catalog-wide funnel events every placement
    benefits from, not specific to this one) - call this once, before writing tracking code,
    then get_placement_health to confirm it actually arrived."""
    try:
        return service.get_tracking_requirements(client_id, slug)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.get("/{slug}/health", response_model=PlacementHealthOut, summary="Is this placement's integration actually working?", responses=_errors(401, 403, 404, 422))
def get_placement_health(slug: str, window_hours: int = Query(24, ge=1, le=24 * 30), client_id: int = Depends(require_scope("placements:read"))):
    """Counts what actually arrived in the last `window_hours` (default 24) - recommend calls,
    impressions, clicks, product views, add-to-carts, purchases - and reports one `ok`/
    `warning`/`missing` check per signal, matching the checklist a developer asking "verify my
    Likyly integration" expects: real facts from LIKYLY's own data, not a guess from reading
    the site's source. `overall` is the worst individual check."""
    try:
        return service.get_health(client_id, slug, window_hours)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.get("/{slug}/recent-events", response_model=RecentIntegrationEventsOut, summary="Raw recent recommend calls and tracked events", responses=_errors(401, 403, 404, 422))
def get_recent_integration_events(slug: str, window_hours: int = Query(24, ge=1, le=24 * 30), limit: int = Query(20, ge=1, le=100), client_id: int = Depends(require_scope("placements:read"))):
    """The individual rows behind get_placement_health's counts - for debugging exactly which
    item/session a recommend call or an event carried, when the aggregate counts alone aren't
    enough to see what's wrong."""
    try:
        return service.get_recent_events(client_id, slug, window_hours, limit)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.post("/{slug}/preview", response_model=PlacementPreviewOut, summary="Try a placement against a sample context", responses=_errors(401, 403, 404, 422))
def preview_placement(slug: str, payload: PlacementPreviewRequest, client_id: int = Depends(require_scope("placements:read"))):
    """Runs the placement's full orchestration (strategy selection, fallback, filters) against
    a sample `context` - never writes anything, no recommendation_id minted, nothing
    attributable. A missing required signal is reported in `errors` (not a 422) so the
    response always says what happened instead of raising mid-workflow."""
    try:
        return service.preview_placement(client_id, slug, payload.context, payload.limit)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.post(
    "/{slug}/recommend", response_model=PlacementRecommendResponse, summary="Get recommendations for a placement",
    responses={200: {"description": "Always contains recommendation_id and items (possibly empty)."}, **_errors(401, 404, 422)},
)
def recommend(slug: str, payload: PlacementRecommendRequest, background_tasks: BackgroundTasks, response: Response, client_id: int = Depends(public_ok_client_id)):
    """The unified recommendation call for a Placement: send whatever context you have
    (current_item_id, user_id, session_id, ...) and LIKYLY runs the placement's configured
    strategy (or "auto" - the same signal-based selection POST /getRec uses), applies its
    fallback and filters, and returns ranked items. `recommendation_id` attributes the
    impression/click/add_to_cart/purchase events that follow, exactly like /getRec. Safe with
    the restricted public key - this is meant to be called from the site itself."""
    try:
        placement, result, items, context_dict = service.run_recommend(client_id, slug, payload.context, payload.limit)
    except service.NotFoundError as error:
        raise _not_found(error)
    except service.ValidationError as error:
        raise _invalid(error)

    from app import _finalize_recommendation  # deferred import - avoids a circular import at module load time (app.py mounts this router)

    recommendation_id = _finalize_recommendation(
        client_id=client_id, product_type=placement["product_type"], strategy=result.strategy_used,
        origin="placement", presented=items, background_tasks=background_tasks, response=response,
        user_id=context_dict.get("user_id"), session_id=context_dict.get("session_id") or context_dict.get("anonymous_id"),
        item_id=context_dict.get("current_item_id"), placement=slug, attempted=result.attempted,
    )
    return {"recommendation_id": recommendation_id, "placement": slug, "strategy_used": result.strategy_used, "items": items}
