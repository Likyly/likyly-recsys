import os
# Some macOS Python installs don't wire the stdlib ssl module up to a trusted CA bundle,
# so urllib-based HTTPS calls (PyJWKClient fetching Supabase's JWKS, below) fail with
# CERTIFICATE_VERIFY_FAILED even though the certs it's asking for are perfectly valid.
# Pointing the process at certifi's bundle fixes it - must happen before anything makes
# an HTTPS call, hence first thing in the file, ahead of the jwt/PyJWKClient import.
import certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import sentry_sdk

# No-op if SENTRY_DSN isn't set (e.g. local dev, or before a Sentry project exists) -
# error tracking is opt-in via env, never required to run the API.
_SENTRY_DSN = os.environ.get("SENTRY_DSN")
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
        # Errors only, no perf tracing - this API's load doesn't warrant tracing overhead
        # yet, and it's a separate cost lever on Sentry's free tier from error events.
        traces_sample_rate=0.0,
        # Request bodies carry user properties and event payloads - personal data that must
        # never end up in an error tracker. (Headers incl. X-API-Key are scrubbed by default.)
        send_default_pii=False,
        max_request_body_size="never",
    )

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Security, Depends, Query, Header, Path, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Annotated, Literal
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import json
import logging
import time
import uuid
import jwt
from jwt import PyJWKClient

from typing import List

import sys
import uvicorn
import pandas as pd

from schemas import (
    RecommendedProduct, VectorRecommendation, User, Message, GenerateModelJobStatus, ModelVersion, ModelStatus,
    ClientSelf,
    ClientAdminView, DailyUsage, ClientRename, ClientUsageSummary, WorkspaceRename, WorkspaceSummary, CatalogIndex,
    EventType, EventTypeCreate, EventTypeUpdate, ImportSummary, Item, ItemUpsert, ItemImportRequest, ItemDeleteRequest, BatchResult, ErrorResponse,
    UserUpsert, UserImportRequest, Event, EventBatch, EventResult, EventBatchResult,
    RecommendationRequest, RecommendationResponse,
    DeveloperKey, DeveloperKeyCreate, DeveloperKeyCreated,
    IdentifyRequest, IdentifyResult,
    DataSource, PlanLimits, Placement, PlacementUpdate, PlacementHealthOut, TrackingRequirementsOut,
    AnalyticsSummaryOut, AnalyticsDetailOut,
    UpgradeRequestCreate, UpgradeRequestReceived,
    SyncRun, SyncTriggerRequest,
)


current_dir = os.path.dirname(os.path.abspath(__file__))
relative_path_utils = "../utils"
absolute_path_utils = os.path.abspath(os.path.join(current_dir, relative_path_utils))
sys.path.insert(0, absolute_path_utils)

#print(sys.path)

from exploreData import *
from modelData import *
from db import (
    init_db,
    get_client_and_scope_by_api_key, ensure_client_id, DEMO_CLIENT_ID, PURCHASE, VIEW,
    list_model_versions, count_interactions_since,
    get_client_by_supabase_user_id, get_client_self_by_id, list_clients_by_supabase_user_id,
    create_client_for_supabase_user, set_client_contact_email,
    regenerate_secret_key, regenerate_public_key, revoke_secret_key, revoke_public_key,
    set_client_active, delete_client, get_client_admin_row, rename_client, set_client_display_name,
    touch_client_usage, list_all_clients_with_usage, get_client_usage_by_day,
    get_active_model_version, count_products_for_client, count_products_in_catalog,
    count_users_in_catalog, count_trainings_today, count_data_sources, list_data_sources, delete_catalog,
    DEFAULT_ITEM_SORT,
    get_client_contact_email, create_upgrade_request, count_upgrade_requests_since,
    list_product_types_for_client, get_client_plan, set_client_plan,
    get_client_event_types, upsert_client_event_type, delete_client_event_type, list_catalogs_for_client,
    EVENT_TIER_WEIGHTS, MANUAL, AUTO, VALID_PLANS, PLAN_FREE, PLAN_PRO, get_plan_limits, plan_limit_message, utcnow,
    KIND_ITEM, KIND_USER, resolve_internal_ids, resolve_external_ids, product_exists,
    get_recent_viewed_work_ids,
    store_recommendation,
    ALLOWED_SCOPES, create_developer_key, list_developer_keys, revoke_developer_key,
    link_session_to_user,
    recommendation_performance_summary, recommendation_performance_timeseries,
    recommendation_performance_by_placement, recommendation_attributed_revenue,
)
from ids import new_recommendation_id
from observability import coerce_request_id, get_logger, log_event, request_id_var
from ingestion import (
    PlanLimitError, delete_items, delete_user, fetch_items, fetch_users, record_events,
    upsert_item, upsert_items_batch, upsert_user_profile, upsert_users_batch,
)
from recommender import (
    present_records, rec_collaborative, rec_content, rec_hybrid, rec_popular, rec_session,
    recommend_auto,
)
from data_sources import router as data_sources_router
from upgrade_requests import UPGRADE_REQUESTS_PER_DAY, notify_team, render_email
from placements import router as placements_router
import placement_service
import data_source_service


# Make sure the products/users/interactions/clients tables exist - harmless no-op if they do.
init_db()
ensure_client_id(DEMO_CLIENT_ID, "LIKYLY Demo")

# The catalog namespace ("movies", "acme-shop", ...) is caller-defined, not a fixed
# enum - a customer's own catalog isn't known in advance. Kept as a string with a
# conservative format so it stays safe to use as a partition key.

# A tenant's own event vocabulary ("purchase", "reservation", "watch", ...) - same
# conservative format as ProductType, since it's also used as a partition key
# (interactions.event_type) and, here, directly in a URL path segment.
EventTypePath = Annotated[str, Path(
    min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$',
    description="Interaction type, e.g. 'purchase', 'reservation', 'watch'",
)]
EventTypeQuery = Annotated[str, Query(
    min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$',
    description="Interaction type, e.g. 'purchase', 'reservation', 'watch'",
)]

#Stopwords dir
stopwords_relative_path = '../../data/stopwords'
stopwords_dir = os.path.abspath(os.path.join(current_dir, stopwords_relative_path))


tags_metadata = [
    {
        "name": "recommendations",
        "description": (
            "**Start here.** `POST /getRec` returns recommended items for a user, an anonymous "
            "session, an item being viewed, or nothing at all - LIKYLY picks the strategy. Every "
            "response carries a `recommendation_id`; send it back on the events that follow to "
            "measure impressions, CTR, conversion and attributed revenue."
        ),
    },
    {
        "name": "recommendations-advanced",
        "description": (
            "One endpoint per strategy (popular, content, collaborative, hybrid, session), for "
            "expert use. They keep their historical bare-array response by default "
            "(`response_format=array`, deprecated) - pass `response_format=object` for the "
            "`{recommendation_id, strategy, items}` envelope. Either way the recommendation_id "
            "is in the `X-Recommendation-Id` response header."
        ),
    },
    {
        "name": "items",
        "description": (
            "Your catalog. An item is anything you recommend - a product, an article, a listing, "
            "a film. Identified by **your own string id**; described by a `title`, an optional "
            "`description` and free-form `properties`. Secret key only: the public key can neither "
            "write nor list the catalog."
        ),
    },
    {
        "name": "users",
        "description": (
            "Optional user profiles (free-form `properties`). You don't need to create a user "
            "before sending events for them. Secret key only - profiles are personal data."
        ),
    },
    {
        "name": "events",
        "description": (
            "Interaction tracking, safe to call from a browser with the public key. Officially "
            "supported types: `impression`, `view`, `click`, `add_to_cart`, `remove_from_cart`, "
            "`purchase` - any other type string is auto-registered on first use. Works for "
            "identified users, anonymous sessions, or both."
        ),
    },
    {
        "name": "models",
        "description": "Trigger collaborative-model training as a background job, poll it, inspect model versions.",
    },
    {
        "name": "data-sources",
        "description": (
            "Configured, recurring catalog integrations (Shopify, a REST API, a CSV, ...) - as "
            "opposed to the one-off writes of `PUT /items`. Authenticated with the secret key, "
            "or a **developer key** (see `/clients/me/developer-keys`) carrying the `sources:read`/"
            "`sources:write` scope - never the restricted public key. This is the surface the "
            "LIKYLY MCP server's admin tools drive."
        ),
    },
    {
        "name": "placements",
        "description": (
            "**Recommended for new integrations.** A Placement is a named, persistent "
            "recommendation configuration - *what* to recommend, *where*, in what context, with "
            "what fallback and filters - configured once (by hand or via the MCP admin tools) "
            "and called by `slug` from then on. `POST /placements/{slug}/recommend` is the "
            "runtime call (public key, same safety as `POST /getRec`); everything else "
            "(create/update/preview/...) needs the secret key or a developer key with "
            "`placements:read`/`placements:write`. Every strategy underneath is one of the "
            "engines already documented under **recommendations** - this is an orchestration "
            "layer, not a new one. `POST /getRec` and the strategy-specific endpoints remain "
            "available as the lower-level, per-call API."
        ),
    },
    {
        "name": "legacy",
        "description": "Frozen Pinecone-based vector endpoints, kept for existing integrations only. Use `content` recommendations instead.",
    },
    {
        "name": "selfServiceClient",
        "description": "Account management for the logged-in website (Supabase session JWT, not an API key).",
    },
    {
        "name": "admin",
        "description": "Operator-only client management (Supabase session JWT with the admin claim).",
    },
]

# Every request must authenticate as a client (tenant): the API key resolves to a
# client_id, and every query/write below is scoped to that client_id. This is what
# keeps two customers' catalogs and events from ever mixing in the shared database,
# even if they happen to pick the same product_type name.
api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False,
    description=(
        "Your API key. Two kinds: the **secret key** (full access - server-side only) and the "
        "**public key** (restricted to recommendations and event tracking - "
        "safe to embed in a web page)."
    ),
)


@dataclass(frozen=True)
class Caller:
    client_id: int
    scope: str  # "secret" | "public"


async def _authenticate(background_tasks: BackgroundTasks, api_key: Optional[str], min_scope: str) -> Caller:
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    result = get_client_and_scope_by_api_key(api_key)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid or inactive X-API-Key")
    client_id, scope = result
    if min_scope == "secret" and scope != "secret":
        raise HTTPException(
            status_code=403,
            detail="This endpoint requires the secret API key - the restricted public key can't be used here",
        )
    # Off the request's critical path - a monitoring side-effect must never slow down
    # actual recommendation serving.
    background_tasks.add_task(touch_client_usage, client_id)
    return Caller(client_id, scope)


async def _resolve_client_id(
    background_tasks: BackgroundTasks, api_key: Optional[str], min_scope: str,
) -> int:
    return (await _authenticate(background_tasks, api_key, min_scope)).client_id


async def get_current_client_id(
    background_tasks: BackgroundTasks, api_key: Optional[str] = Security(api_key_header),
) -> int:
    """Requires the secret (full-access) key. Default dependency for anything that reads
    PII (users/purchases/ratings/page views) or writes to the catalog/model."""
    return await _resolve_client_id(background_tasks, api_key, "secret")


async def get_current_client_id_public_ok(
    background_tasks: BackgroundTasks, api_key: Optional[str] = Security(api_key_header),
) -> int:
    """Accepts either key. Only used on endpoints safe to call directly from a browser
    with the restricted public key: reading recommendations, and recording
    events - see the two-tier key architecture in db.py's ClientModel."""
    return await _resolve_client_id(background_tasks, api_key, "public")


async def get_caller_public_ok(
    background_tasks: BackgroundTasks, api_key: Optional[str] = Security(api_key_header),
) -> Caller:
    """Same as get_current_client_id_public_ok, for the few endpoints whose behavior also
    depends on WHICH key was used (POST /getRec's `debug`)."""
    return await _authenticate(background_tasks, api_key, "public")


_CATALOG_PATTERN = r'^[a-zA-Z0-9_-]+$'
DEFAULT_CATALOG = "default"

ProductType = Annotated[str, Query(
    min_length=1, max_length=64, pattern=_CATALOG_PATTERN,
    description="Catalog namespace, e.g. 'movies' or a customer's own catalog name",
)]

# How long a client's list of catalogs is remembered when resolving an omitted data_product_type.
_CATALOG_CACHE_SECONDS = 30
_catalog_cache: dict[int, tuple[float, list[str]]] = {}


def _catalogs_of(client_id: int) -> list[str]:
    cached = _catalog_cache.get(client_id)
    if cached and time.monotonic() - cached[0] < _CATALOG_CACHE_SECONDS:
        return cached[1]
    catalogs = list_catalogs_for_client(client_id)
    _catalog_cache[client_id] = (time.monotonic(), catalogs)
    return catalogs


def resolve_catalog(
    data_product_type: Optional[str] = Query(
        None, min_length=1, max_length=64, pattern=_CATALOG_PATTERN,
        description=(
            "Catalog namespace - keeps several catalogs of one account apart. **Optional**: when "
            f"omitted, the account's only catalog is used, or `{DEFAULT_CATALOG}` if it has none yet. "
            "An account with several catalogs must say which (`422` otherwise)."
        ),
        examples=["shop"],
    ),
    api_key: Optional[str] = Security(api_key_header),
) -> str:
    """Query parameter of every API-key route. Explicit always wins. Omitted: the account's single
    catalog (items or events), else "default" for a brand-new account; several -> 422, never a
    silent guess between them. An unknown/missing key falls through to "default" - the auth
    dependency of the route rejects it right after with the proper 401."""
    if data_product_type is not None:
        return data_product_type
    resolved = get_client_and_scope_by_api_key(api_key) if api_key else None
    if resolved is None:
        return DEFAULT_CATALOG
    catalogs = _catalogs_of(resolved[0])
    if len(catalogs) > 1:
        raise HTTPException(
            status_code=422,
            detail="data_product_type is required: this account has several catalogs - say which one you mean",
        )
    return catalogs[0] if catalogs else DEFAULT_CATALOG


# Same parameter, resolved as above - used by every route authenticated with an API key.
# (The account/admin routes authenticated by Supabase JWT keep the plain, required ProductType.)
Catalog = Annotated[str, Depends(resolve_catalog)]


# Separate from the X-API-Key mechanism above: this verifies a Supabase-issued session
# JWT (Authorization: Bearer ...) for the self-service "log in on the website, get an
# API key" flow - only used by the /clients/me* endpoints, never for the recsys
# endpoints themselves. Verified against Supabase's public JWKS (asymmetric ES256/RS256
# signing keys) - no shared secret needed or stored on this side.
SUPABASE_URL = os.environ.get("SUPABASE_URL")
_supabase_jwks_client = PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json") if SUPABASE_URL else None


def _decode_supabase_jwt(authorization: Optional[str]) -> dict:
    if not _supabase_jwks_client:
        raise HTTPException(status_code=500, detail="SUPABASE_URL not configured on the server")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization.removeprefix("Bearer ")
    try:
        signing_key = _supabase_jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, algorithms=["ES256", "RS256"], audience="authenticated")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid session token")


async def get_current_supabase_user_id(authorization: Optional[str] = Header(None)) -> str:
    return _decode_supabase_jwt(authorization)["sub"]


async def get_current_supabase_identity(authorization: Optional[str] = Header(None)) -> tuple[str, Optional[str]]:
    """Same verification as get_current_supabase_user_id, also returning the account's
    email - Supabase includes it as a standard JWT claim, so this avoids a separate call
    to the Supabase Admin API just to show "whose account is this" in /admin/clients."""
    payload = _decode_supabase_jwt(authorization)
    return payload["sub"], payload.get("email")


async def get_current_admin_user_id(authorization: Optional[str] = Header(None)) -> str:
    """Same JWT verification as get_current_supabase_user_id, plus an app_metadata.
    is_admin check - app_metadata (unlike user_metadata) can only be set via Supabase's
    Admin API or dashboard, never by the user themselves, so this can't be
    self-escalated by editing one's own profile."""
    payload = _decode_supabase_jwt(authorization)
    if not payload.get("app_metadata", {}).get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload["sub"]


def to_records_or_404(data, not_found_status=404):
    if "Error" in data.columns:
        raise HTTPException(status_code=not_found_status, detail=data["Error"].iloc[0])
    return json.loads(data.to_json(orient="records", date_format="iso"))

# In-memory store for background training jobs. Fine for this single-process demo API;
# would need a shared store (DB/Redis) behind multiple workers or processes.
GENERATE_MODEL_JOBS: dict[str, dict] = {}


def run_generate_model_job(job_id: str, client_id: int, product_type: str, triggered_by: str = MANUAL):
    GENERATE_MODEL_JOBS[job_id]["status"] = "running"
    try:
        result = train_and_maybe_promote_model(product_type, client_id=client_id, triggered_by=triggered_by)

        if result["promoted"]:
            detail = f"Model trained (precision@10={result['precision_at_k']:.4f}) and promoted to production"
        else:
            detail = (
                f"Model trained (precision@10={result['precision_at_k']:.4f}) but NOT promoted - "
                f"previous active version scored {result['previous_precision_at_k']:.4f}, kept in production"
            )

        GENERATE_MODEL_JOBS[job_id].update(
            status="completed",
            detail=detail,
            version_id=result["version_id"],
            precision_at_k=result["precision_at_k"],
            promoted=result["promoted"],
        )
    except Exception as error:
        # This runs as a background task - an uncaught exception here would just vanish
        # into the event loop instead of surfacing anywhere, so report it to Sentry
        # explicitly rather than relying on its default unhandled-exception capture.
        sentry_sdk.capture_exception(error)
        # The raw exception text (file paths, SQL, library internals) stays in the logs /
        # Sentry; the job status the customer polls only gets a generic reason.
        log_event("training_failed", level=logging.ERROR, job_id=job_id, client_id=client_id, error_type=type(error).__name__)
        GENERATE_MODEL_JOBS[job_id].update(status="failed", detail=f"Training failed ({type(error).__name__}) - contact support with this job_id")


# Automatic retraining, triggered by accumulated interaction volume rather than a blind
# time-based cron (the previous approach here - a 5-minute APScheduler job hitting
# /generateModel unconditionally - was defined but never actually wired into the app,
# since `lifespan` was never passed to FastAPI(); it also had no concept of "is there
# actually new signal worth training on"). Every (client_id, product_type) pair that
# crosses this many new interactions since its last training run gets an automatic
# background retrain, gated through the same promotion logic as a manual /generateModel
# call - a bad automatic retrain still can't degrade production.
AUTO_RETRAIN_INTERACTION_THRESHOLD = 50
_auto_retrain_in_progress: set[tuple[int, str]] = set()

# Most recent auto-retrain skipped for hitting the free-tier daily quota, per
# (client_id, product_type) - in-memory only (fine for this single-process demo API, same
# tradeoff as GENERATE_MODEL_JOBS below). Lets the client dashboard show *why* the model
# didn't update today, instead of silently doing nothing.
_auto_retrain_skips: dict[tuple[int, str], dict] = {}


def run_auto_retrain_job(job_id: str, client_id: int, product_type: str, key: tuple[int, str]):
    try:
        run_generate_model_job(job_id, client_id, product_type, triggered_by=AUTO)
    finally:
        _auto_retrain_in_progress.discard(key)


def maybe_trigger_auto_retrain(client_id: int, product_type: str, background_tasks: BackgroundTasks) -> None:
    key = (client_id, product_type)
    if key in _auto_retrain_in_progress:
        return  # a retrain for this pair is already running - don't pile on

    active = get_active_model_version(client_id, product_type)
    since = active["trained_at"] if active else None
    new_interactions = count_interactions_since(client_id, product_type, since)
    if new_interactions < AUTO_RETRAIN_INTERACTION_THRESHOLD:
        return

    auto_retrain_daily_limit = get_plan_limits(get_client_plan(client_id))["auto_retrain_daily_limit"]
    if auto_retrain_daily_limit is not None and count_trainings_today(client_id, product_type, AUTO) >= auto_retrain_daily_limit:
        _auto_retrain_skips[key] = {
            "skipped_at": utcnow(),
            "reason": (
                f"Free plan limit reached: {auto_retrain_daily_limit} automatic "
                f"retrain(s) per day. {new_interactions} new interactions are waiting - "
                "the model will catch up on tomorrow's automatic retrain, or upgrade your plan."
            ),
        }
        return

    _auto_retrain_skips.pop(key, None)
    _auto_retrain_in_progress.add(key)
    job_id = str(uuid.uuid4())
    GENERATE_MODEL_JOBS[job_id] = {
        "job_id": job_id, "client_id": client_id, "status": "queued", "data_product_type": product_type,
        "detail": f"Auto-triggered: {new_interactions} new interactions since last training",
        "version_id": None, "precision_at_k": None, "promoted": None,
    }
    background_tasks.add_task(run_auto_retrain_job, job_id, client_id, product_type, key)


app_description = (
    "Recommendations for any web application: send your **items** (catalog), your **users** "
    "and their **events**, get back a ranked list of items.\n\n"
    "### Quick start\n"
    "1. `PUT /items/{item_id}` (or `POST /items/import`) - push your catalog, using your own ids.\n"
    "2. `POST /events/{event_type}` (or `/events/batch`) - track what visitors do.\n"
    "3. Create a **Placement** (`POST /placements` - see the `placements` tag) and call "
    "`POST /placements/{slug}/recommend` from then on - the recommended way to ask for "
    "recommendations for a given spot on your site. `POST /getRec` (this tag) remains "
    "available as the advanced, per-call, no-persistent-config alternative. Either way you get "
    "a `recommendation_id`; send it back on the `impression` / `click` / `add_to_cart` / "
    "`purchase` events to attribute them.\n\n"
    "### Concepts\n"
    "- **Ids are your own strings** (`user_123`, `SKU-NIKE-001`, a UUID, `gid://shopify/Product/123456`). "
    "Integers are still accepted for backward compatibility and treated as their decimal string.\n"
    "- **session_id** identifies an anonymous visitor: no login needed to track or recommend. "
    "An event may carry both `user_id` and `session_id`.\n"
    "- **placement** is a free-form label of where recommendations appear (`homepage`, `cart`, ...). "
    "Not a resource, no list to maintain.\n"
    "- **data_product_type** is the catalog namespace (query parameter), used to keep several catalogs of one "
    "account apart. It is **optional**: omit it and the account's only catalog is used (`default` for a new "
    "account); an account with several catalogs must name one (`422` otherwise).\n"
    "- Multi-tenant: every request authenticates via `X-API-Key` to one account whose data is fully isolated.\n"
    "- Two-tier keys: the **secret key** (full access, server-side only) and a restricted **public key** "
    "(recommendations and event tracking only) safe to embed in client-side JS.\n\n"
    "### Under the hood\n"
    "TF-IDF + pgvector semantic embeddings for content similarity, implicit ALS for collaborative "
    "filtering with model versioning/promotion gating, popularity as the cold-start fallback. "
    "`POST /getRec` chooses between them for you; the strategy-specific `GET /getRec/*` endpoints "
    "remain for expert use. Legacy Pinecone-based vector endpoints are frozen.\n\n"
    "### Errors\n"
    "Every error body is `{\"detail\": ..., \"request_id\": ...}`; the same id is in the `X-Request-ID` "
    "response header. Send your own `X-Request-ID` to correlate with your logs.\n\n"
    "### Rate limits (enforced at the gateway, per client IP)\n"
    "`/getRec*`: 100 req/s (burst 100) · `/events*`: 100 req/s (burst 200) · catalog & users "
    "(`/items*`, `/users*`, `/products*`): 20 req/s (burst 40) · admin & account: 5 req/s "
    "(burst 10) · CSV import: 2 req/s (burst 5) · everything else: 20 req/s (burst 10). "
    "Exceeding a limit returns `429`. Batch endpoints (`/events/batch`, "
    "`/items/import`, `/users/import`) exist to keep server-side traffic well under these."
)

app = FastAPI(title="LIKYLY Recommendations API",
              description=app_description,
              version="0.2.0",
              openapi_tags=tags_metadata,
              root_path="/recsys-api",
              servers=[{"url": "https://api.likyly.com", "description": "Production"}],
              # Operation ids = handler names (items_upsert, recommendations_get, ...) instead of
              # FastAPI's default "name_path_method" noise - stable ids are what SDK generators key on.
              generate_unique_id_function=lambda route: route.name,
              # Swagger UI's default /docs is replaced below with Scalar - same openapi.json,
              # friendlier reading/"try it" experience for the public /sdks fallback link.
              docs_url=None,
              )
app.include_router(data_sources_router)
app.include_router(placements_router)


def _errors(*codes: int) -> dict:
    """OpenAPI `responses` entries for the error statuses an operation can return - every
    error body shares the ErrorResponse shape (detail + request_id)."""
    docs = {
        401: "Missing, invalid or revoked `X-API-Key`.",
        403: "The public key can't do this (secret key required), or a plan limit was reached.",
        404: "The referenced resource does not exist for this account.",
        422: "The request is malformed - `detail` lists the offending fields.",
    }
    responses: dict = {code: {"model": ErrorResponse, "description": docs[code]} for code in codes}
    responses[429] = {"description": "Rate limit exceeded at the API gateway - retry shortly."}
    return responses


@app.get("/", summary="API root", description="Liveness message; also a cheap way to check the API is reachable.", tags=["models"], include_in_schema=False)
async def root():
    return {"message": "LIKYLY recommendations API - content-based (TF-IDF + pgvector semantic embeddings) and collaborative filtering (implicit ALS) recommendations"}

# Scalar, not Swagger UI (docs_url=None above): a static, account-less page that reads the
# same openapi.json and renders an interactive reference with "Try it" - the public, no-login
# surface for whoever has no SDK for their language (see likyly-website's /sdks page). Chosen
# deliberately over self-hosting a full API-client app (Hoppscotch): that has its own user
# accounts/admin panel and the self-hosted Community edition has no way to restrict signups,
# which is real exposure a stateless spec renderer like this one simply doesn't have.
_SCALAR_HTML = """<!doctype html>
<html>
  <head>
    <title>LIKYLY API Reference</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
  </head>
  <body>
    <script id="api-reference" data-url="/openapi.json"></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
  </body>
</html>"""


@app.get("/docs", summary="Interactive API reference (Scalar)", include_in_schema=False)
async def api_reference():
    return HTMLResponse(_SCALAR_HTML)

# An account can own several workspaces - every /clients/me/* route below (except POST
# /clients/me itself, which bootstraps the very first one and has nothing to select yet)
# accepts this to say which one it means. Omitted: the primary (oldest) workspace, so every
# dashboard call written before multi-workspace existed keeps working unchanged. See
# _resolve_my_client_id, which validates a given value against the account's own workspaces.
WorkspaceIdHeader = Annotated[Optional[int], Header(alias="X-Workspace-Id", description="Which of this account's workspaces this call is about. Omitted: the primary (oldest) one.")]

@app.post("/clients/me", tags=["selfServiceClient"], response_model=ClientSelf, summary="Get or create my account", responses=_errors(401))
async def get_or_create_my_client(identity: tuple[str, Optional[str]] = Depends(get_current_supabase_identity)):
    """Called from the website once a Supabase user is logged in. First call for a given
    account provisions a client + both keys (each shown once); later calls just confirm
    the existing client without re-exposing them - see /clients/me/regenerate-secret-key
    and /clients/me/regenerate-public-key for that. Also keeps contact_email in sync from
    the JWT on every call, not just creation, so it self-heals for accounts created
    before this field existed and stays correct if the email ever changes."""
    supabase_user_id, email = identity
    existing = get_client_by_supabase_user_id(supabase_user_id)
    if existing:
        set_client_contact_email(existing["id"], email)
        return {
            "client_id": existing["id"], **{k: v for k, v in existing.items() if k != "id"},
            "product_types": list_product_types_for_client(existing["id"]),
        }

    client_id, raw_secret_key, raw_public_key = create_client_for_supabase_user(
        name=f"li_{uuid.uuid4().hex[:16]}", supabase_user_id=supabase_user_id, email=email,
    )
    created = get_client_by_supabase_user_id(supabase_user_id)
    return {
        "client_id": client_id, "name": created["name"], "display_name": created["display_name"],
        "secret_key": raw_secret_key, "public_key": raw_public_key,
        "secret_key_hint": created["secret_key_hint"], "public_key_hint": created["public_key_hint"],
        "has_public_key": created["has_public_key"],
        "secret_key_rotated_at": created["secret_key_rotated_at"],
        "public_key_rotated_at": created["public_key_rotated_at"],
        "product_types": [],
    }

@app.get("/clients/me", tags=["selfServiceClient"], response_model=ClientSelf, summary="Read my workspace", responses=_errors(401, 404))
async def get_my_client(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """The read-only counterpart of POST /clients/me: returns the workspace (name, key status,
    indexes) but never creates it and never carries a raw key. Safe to call from anywhere in
    the dashboard - POST /clients/me is not, since the call that provisions the account is the
    only one that can show the keys. `404` when the account has no workspace yet."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    existing = get_client_self_by_id(client_id)
    return {
        "client_id": existing["id"], **{k: v for k, v in existing.items() if k != "id"},
        "product_types": list_product_types_for_client(existing["id"]),
    }

@app.patch("/clients/me", tags=["selfServiceClient"], response_model=ClientSelf, summary="Set my workspace's display name", responses=_errors(401, 404, 422))
async def rename_my_client(payload: WorkspaceRename, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Sets the workspace's display name - the name the console shows. The technical `name` ("li_" +
    code) is left alone: it stays the identifier to quote to support, and nothing (keys, indexes,
    models) is keyed on either."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    set_client_display_name(client_id, payload.display_name)
    return await get_my_client(supabase_user_id, x_workspace_id)

@app.get("/clients/me/workspaces", tags=["selfServiceClient"], response_model=List[WorkspaceSummary], summary="List every workspace I own", responses=_errors(401, 404))
async def list_my_workspaces(supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Every workspace this account owns, oldest (primary) first - the console's workspace
    switcher. Not X-Workspace-Id-gated: there's nothing to select for "list them all"."""
    owned = list_clients_by_supabase_user_id(supabase_user_id)
    if not owned:
        raise HTTPException(status_code=404, detail="No client for this account yet - call POST /clients/me first")
    return owned

@app.post("/clients/me/workspaces", tags=["selfServiceClient"], response_model=ClientSelf, status_code=201, summary="Create an additional workspace", responses=_errors(401, 403, 404))
async def create_my_workspace(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Creates a new, independent workspace owned by this same account - its own catalogs, keys
    and plan (starts on Free), not a copy of the current one. Gated by the *currently selected*
    workspace's plan: Free allows exactly one workspace total (workspace_limit=1, i.e. none
    beyond the one already owned); Pro raises the cap - see /clients/me/plans. Same response
    shape as POST /clients/me's creation branch: a fresh key pair, shown once."""
    current_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    plan = get_client_plan(current_id)
    limit = get_plan_limits(plan)["workspace_limit"]
    owned = list_clients_by_supabase_user_id(supabase_user_id)
    if limit is not None and len(owned) >= limit:
        raise HTTPException(status_code=403, detail=plan_limit_message(plan, f"{limit} workspace(s) owned"))

    email = get_client_contact_email(current_id)
    client_id, raw_secret_key, raw_public_key = create_client_for_supabase_user(
        name=f"li_{uuid.uuid4().hex[:16]}", supabase_user_id=supabase_user_id, email=email,
    )
    created = get_client_self_by_id(client_id)
    return {
        "client_id": client_id, "name": created["name"], "display_name": created["display_name"],
        "secret_key": raw_secret_key, "public_key": raw_public_key,
        "secret_key_hint": created["secret_key_hint"], "public_key_hint": created["public_key_hint"],
        "has_public_key": created["has_public_key"],
        "secret_key_rotated_at": created["secret_key_rotated_at"],
        "public_key_rotated_at": created["public_key_rotated_at"],
        "product_types": [],
    }

@app.get("/clients/me/indexes", tags=["selfServiceClient"], response_model=List[CatalogIndex], summary="List my indexes", responses=_errors(401, 404))
async def list_my_indexes(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """The workspace's indexes - one per catalog namespace (product_type) it holds items in, with
    the item count and how many data sources feed it. A data source that has not synced anything
    yet does not create an index until its first item lands."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    sources_per_index: dict[str, int] = {}
    for source in list_data_sources(client_id):
        sources_per_index[source["product_type"]] = sources_per_index.get(source["product_type"], 0) + 1
    return [
        {"name": name, "product_count": count_products_in_catalog(client_id, name), "data_source_count": sources_per_index.get(name, 0)}
        for name in sorted(list_product_types_for_client(client_id))
    ]

IndexNamePath = Annotated[str, Path(min_length=1, max_length=64, pattern=_CATALOG_PATTERN, description="An index's name, as GET /clients/me/indexes lists it.")]
IndexItemIdPath = Annotated[str, Path(
    min_length=1, max_length=255,
    description="An item's own id within the index, as GET .../items lists it (`item_id`).",
    examples=["SKU-NIKE-001"],
)]
ItemSortQuery = Annotated[
    Literal["updated_at", "-updated_at", "title", "-title", "price", "-price"],
    Query(description="Field to sort by; a leading `-` reverses it. Rows missing the field sort last either way."),
]

@app.get("/clients/me/indexes/{index_name}/items", tags=["selfServiceClient"], response_model=List[Item], summary="Browse an index's items", responses=_errors(401, 404, 422))
async def list_my_index_items(
    response: Response, index_name: IndexNamePath,
    limit: int = Query(20, ge=1, le=100, description="Page size."),
    offset: int = Query(0, ge=0, description="Rows to skip."),
    search: Optional[str] = Query(None, max_length=200, description="Matched against title and description (case-insensitive, substring)."),
    sort: ItemSortQuery = DEFAULT_ITEM_SORT,
    supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """A read-only page of this index's items - what the "Catalogue" screen shows to confirm a
    sync actually landed the products it claimed to, and to find one by name. The total item
    count *for this search* is in the `X-Total-Count` response header (same convention as GET
    /items, the secret-key equivalent this mirrors for the dashboard). An index name that isn't
    this workspace's own, or doesn't exist, reads as empty rather than another tenant's data -
    `fetch_items` is client_id-scoped underneath."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    response.headers["X-Total-Count"] = str(count_products_in_catalog(client_id, index_name, search=search))
    return fetch_items(client_id, index_name, limit=limit, offset=offset, search=search, sort=sort)

@app.put("/clients/me/indexes/{index_name}/items/{item_id}", tags=["selfServiceClient"], response_model=Item, summary="Create or replace an item", responses={201: {"model": Item, "description": "The item did not exist and was created."}, **_errors(401, 403, 404, 422)})
async def upsert_my_index_item(
    item_id: IndexItemIdPath, index_name: IndexNamePath, payload: ItemUpsert, response: Response,
    supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """Edits one item from the "Catalogue" browser - same idempotent create-or-replace semantics
    as PUT /items/{item_id} (the secret-key equivalent this mirrors), the whole item is the body.
    A brand-new item here also counts against the free plan's product cap, same as any other
    write path - see PlanLimitError below."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    try:
        created = upsert_item(client_id, index_name, item_id, payload)
    except PlanLimitError as error:
        raise HTTPException(status_code=403, detail=str(error))
    if created:
        response.status_code = 201
    return fetch_items(client_id, index_name, item_id=item_id)[0]

@app.delete("/clients/me/indexes/{index_name}/items/{item_id}", tags=["selfServiceClient"], response_model=Message, summary="Delete an item", responses=_errors(401, 404, 422))
async def delete_my_index_item(index_name: IndexNamePath, item_id: IndexItemIdPath, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Removes this item's catalog profile from the "Catalogue" browser - its past interactions
    (still valid collaborative signal) are kept, same as DELETE /items/{item_id}."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    if delete_items(client_id, index_name, [item_id]):
        raise HTTPException(status_code=404, detail=f"No item '{item_id}' in index '{index_name}'")
    return {"message": f"Item '{item_id}' deleted"}

@app.delete("/clients/me/indexes/{index_name}", tags=["selfServiceClient"], response_model=Message, summary="Delete an index", responses=_errors(401, 404))
async def delete_my_index(index_name: IndexNamePath, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Removes every item of this index - the "Supprimer" danger-zone action on the "Catalogue"
    screen. Irreversible from here (no undo); the data sources (connectors) that fed this index
    stay configured, only pointing at an index with nothing in it until a sync runs again. A
    name with nothing to delete is a 404, same as any other not-found here."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    if index_name not in list_product_types_for_client(client_id):
        raise HTTPException(status_code=404, detail=f"No index '{index_name}' for this account")
    removed = delete_catalog(client_id, index_name)
    return {"message": f"Index '{index_name}' deleted ({removed} item(s))"}

@app.get("/clients/me/placements", tags=["selfServiceClient"], response_model=List[Placement], summary="List my placements", responses=_errors(401, 404))
async def list_my_placements(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """The workspace's recommendation placements - what "Recommandations" shows to confirm the
    agent's configuration actually landed. Same rows as GET /placements (the secret/developer-key
    admin surface), read-only and Supabase-session-gated for the dashboard instead."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    return placement_service.list_placements(client_id)

@app.patch("/clients/me/placements/{slug}", tags=["selfServiceClient"], response_model=Placement, summary="Update a placement", responses=_errors(401, 404, 422))
async def update_my_placement(
    slug: str, payload: PlacementUpdate,
    supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """Same partial-update semantics as PATCH /placements/{slug} (the secret/developer-key
    admin route this mirrors): only the fields sent are changed - e.g. {"enabled": false} to
    turn a placement off without touching anything else, from the "Recommandations" page's
    own enable/disable and edit controls, no agent needed for the basics."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    try:
        return placement_service.update_placement(client_id, slug, payload)
    except placement_service.NotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except placement_service.ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error))

@app.get("/clients/me/placements/{slug}/tracking-requirements", tags=["selfServiceClient"], response_model=TrackingRequirementsOut, summary="Which events this placement needs instrumented", responses=_errors(401, 404, 422))
async def get_my_placement_tracking_requirements(
    slug: str, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """Same as GET /placements/{slug}/tracking-requirements (admin) - which events to send and
    why. Shown alongside the "Par prompt"/"Par code" integration tabs so it's explicit which
    events actually matter for this placement, not just how to call /recommend."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    try:
        return placement_service.get_tracking_requirements(client_id, slug)
    except placement_service.NotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))

@app.get("/clients/me/placements/{slug}/health", tags=["selfServiceClient"], response_model=PlacementHealthOut, summary="Is this placement's integration actually working?", responses=_errors(401, 404, 422))
async def get_my_placement_health(
    slug: str, window_hours: int = Query(24, ge=1, le=24 * 30),
    supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """The same checklist validate_integration (the MCP tool) reports - whether recommend calls,
    impressions, clicks and the rest of the funnel actually arrived for this placement, over the
    last `window_hours`. Structured data here; the MCP tool is what renders it as ✓/⚠/✗ for an
    agent to read out."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    try:
        return placement_service.get_health(client_id, slug, window_hours)
    except placement_service.NotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))

def _resolve_analytics_window(plan: str, start: Optional[date], end: Optional[date], default_days: int) -> tuple[datetime, datetime, date, date]:
    """Turns a caller-chosen [start, end] (either may be omitted) into a concrete UTC datetime
    range, clamped to this plan's analytics_window_days cap - never trust the client's
    requested range beyond what the plan allows, same principle as every other limit in this
    API. Returns (since, until, start_date, end_date); the last two are what the response
    echoes back so the caller knows what was actually used."""
    max_days = get_plan_limits(plan)["analytics_window_days"]
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

@app.get("/clients/me/analytics/summary", tags=["selfServiceClient"], response_model=AnalyticsSummaryOut, summary="Recommendation performance - totals", responses=_errors(401, 404))
async def get_my_analytics_summary(
    start: Optional[date] = Query(None, description="Inclusive start date (YYYY-MM-DD). Defaults to 7 days before `end`."),
    end: Optional[date] = Query(None, description="Inclusive end date (YYYY-MM-DD). Defaults to today."),
    supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """Recommendations served, impressions, clicks and CTR - the "Performance" page's summary
    cards, always available on any plan. The [start, end] range is server-clamped to this
    workspace's plan (Free: 7-day span, Pro: 90) regardless of what was asked for - never trust
    the client's requested range beyond the plan's own cap, same principle as every other limit
    here."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    plan = get_client_plan(client_id)
    since, until, start_date, end_date = _resolve_analytics_window(plan, start, end, default_days=7)
    summary = recommendation_performance_summary(client_id, since, until=until)
    return {"start": start_date.isoformat(), "end": end_date.isoformat(), **summary}

@app.get("/clients/me/analytics/detail", tags=["selfServiceClient"], response_model=AnalyticsDetailOut, summary="Recommendation performance - trend, by placement, revenue", responses=_errors(401, 403, 404))
async def get_my_analytics_detail(
    start: Optional[date] = Query(None, description="Inclusive start date (YYYY-MM-DD). Defaults to 30 days before `end`."),
    end: Optional[date] = Query(None, description="Inclusive end date (YYYY-MM-DD). Defaults to today."),
    supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """The depth behind the summary - a day-by-day trend, a per-placement breakdown, and
    revenue attributed to a recommendation (purchases that carry a recommendation_id).
    Pro only: 403 on Free, same plan_limit_message wording as every other gated feature."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    plan = get_client_plan(client_id)
    limits = get_plan_limits(plan)
    if not limits["analytics_detail"]:
        raise HTTPException(status_code=403, detail=plan_limit_message(plan, "detailed performance data (trend, by placement, revenue)"))
    since, until, start_date, end_date = _resolve_analytics_window(plan, start, end, default_days=30)
    return {
        "start": start_date.isoformat(), "end": end_date.isoformat(),
        "timeseries": recommendation_performance_timeseries(client_id, since, until=until),
        "by_placement": recommendation_performance_by_placement(client_id, since, until=until),
        "revenue": recommendation_attributed_revenue(client_id, since, until=until),
    }

@app.post("/clients/me/regenerate-secret-key", tags=["selfServiceClient"], response_model=ClientSelf, summary="Regenerate my secret key", responses=_errors(401, 404))
async def regenerate_my_secret_key(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Invalidates the current secret key and issues a new one - the only way to recover
    from a lost key, since the raw value is never stored."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    existing = get_client_self_by_id(client_id)

    raw_key = regenerate_secret_key(existing["id"])
    updated = get_client_self_by_id(client_id)
    return {
        "client_id": existing["id"], "name": existing["name"], "display_name": existing["display_name"], "secret_key": raw_key,
        "secret_key_hint": updated["secret_key_hint"], "public_key_hint": updated["public_key_hint"],
        "has_public_key": updated["has_public_key"],
        "secret_key_rotated_at": updated["secret_key_rotated_at"],
        "public_key_rotated_at": updated["public_key_rotated_at"],
        "product_types": list_product_types_for_client(existing["id"]),
    }

@app.post("/clients/me/regenerate-public-key", tags=["selfServiceClient"], response_model=ClientSelf, summary="Regenerate my public key", responses=_errors(401, 404))
async def regenerate_my_public_key(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Same as regenerate-secret-key, for the restricted public key."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    existing = get_client_self_by_id(client_id)

    raw_key = regenerate_public_key(existing["id"])
    updated = get_client_self_by_id(client_id)
    return {
        "client_id": existing["id"], "name": existing["name"], "display_name": existing["display_name"], "public_key": raw_key,
        "secret_key_hint": updated["secret_key_hint"], "public_key_hint": updated["public_key_hint"],
        "has_public_key": True,
        "secret_key_rotated_at": updated["secret_key_rotated_at"],
        "public_key_rotated_at": updated["public_key_rotated_at"],
        "product_types": list_product_types_for_client(existing["id"]),
    }

@app.get("/clients/me/usage", tags=["selfServiceClient"], response_model=ClientUsageSummary, summary="My plan usage", responses=_errors(401, 404))
async def get_my_usage(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Account-wide plan/product-count numbers for the self-service dashboard - separate
    from /clients/me/models/status, which is scoped to one product_type."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    return _usage_summary(client_id)

def _usage_summary(client_id: int) -> dict:
    plan = get_client_plan(client_id)
    limits = get_plan_limits(plan)
    return {
        "plan": plan,
        "product_count": count_products_for_client(client_id),
        "product_limit": limits["product_limit"],
        "index_count": len(list_product_types_for_client(client_id)),
        "index_limit": limits["index_limit"],
        "data_source_count": count_data_sources(client_id),
        "data_source_limit": limits["data_source_limit"],
    }

@app.get("/clients/me/plans", tags=["selfServiceClient"], response_model=List[PlanLimits], summary="Compare the Free and Pro plans", responses=_errors(401))
async def list_plans(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """What each self-service plan allows, straight from the limits the API enforces - so the
    upgrade page can show what Pro adds without hard-coding numbers that would drift. `null` =
    no cap. The internal `unlimited` plan (demo/pilot accounts) is not offered."""
    return [{"plan": plan, **get_plan_limits(plan)} for plan in (PLAN_FREE, PLAN_PRO)]

@app.post(
    "/clients/me/upgrade-request", tags=["selfServiceClient"], response_model=UpgradeRequestReceived, status_code=201,
    summary="Request a plan upgrade",
    responses={**_errors(401, 404, 422), 429: {"model": ErrorResponse, "description": "Too many upgrade requests from this workspace in the last 24 hours."}},
)
async def create_my_upgrade_request(payload: UpgradeRequestCreate, background_tasks: BackgroundTasks, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """There is no online payment yet: this is how a workspace asks for the Pro plan. The answers are
    stored, then emailed to the team (contact@likyly.com) with the workspace's plan and current
    usage attached - the email goes out in the background, so a mail problem never fails the request.
    A few requests per day per workspace, no more."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    existing = get_client_self_by_id(client_id)
    if count_upgrade_requests_since(client_id, utcnow() - timedelta(hours=24)) >= UPGRADE_REQUESTS_PER_DAY:
        raise HTTPException(status_code=429, detail="Vous avez déjà envoyé plusieurs demandes aujourd'hui - nous revenons vers vous très vite par email.")

    form = payload.model_dump(exclude={"consent"})
    row = create_upgrade_request(client_id, form)
    account_email = get_client_contact_email(client_id)
    subject, body = render_email(row["id"], existing, _usage_summary(client_id), account_email, form)
    background_tasks.add_task(notify_team, row["id"], subject, body, account_email)
    log_event("upgrade_request_received", client_id=client_id, upgrade_request_id=row["id"])
    return {"id": row["id"], "created_at": row["created_at"]}

@app.get("/clients/me/data-sources", tags=["selfServiceClient"], response_model=List[DataSource], summary="List my data sources", responses=_errors(401, 404))
async def list_my_data_sources(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """The catalog connection(s) attached to this workspace - what the dashboard's "Données"
    tab shows, alongside /clients/me/usage's data_source_count/data_source_limit. A read-only
    mirror of GET /data-sources (which needs the secret/developer key, for a coding agent) -
    this one is Supabase-session-gated instead, for the logged-in dashboard user; both list
    the same rows, scoped to the same client_id, credentials never included either way."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    return list_data_sources(client_id)

@app.post("/clients/me/data-sources/{data_source_id}/sync", tags=["selfServiceClient"], response_model=SyncRun, status_code=202, summary="Trigger a sync", responses=_errors(401, 404, 422))
async def sync_my_data_source(data_source_id: int, payload: SyncTriggerRequest, background_tasks: BackgroundTasks, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Manually triggers a sync for one of this workspace's data sources - the dashboard's
    "Synchroniser maintenant" button. A Supabase-session-gated mirror of POST /data-sources/{id}/sync
    (which needs the secret/developer key, for a coding agent): same trigger_sync underneath, so the
    same rules apply - 404 if the source doesn't belong to this account, 422 for a push-only source
    type (Shopify webhooks, WooCommerce) that has nothing to trigger, 429 for the cooldown floor or
    the plan's daily cap (see /clients/me/plans' manual_sync_daily_limit)."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    try:
        run = data_source_service.trigger_sync(client_id, data_source_id, payload.mode)
    except data_source_service.NotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except data_source_service.ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except data_source_service.RateLimitError as error:
        raise HTTPException(status_code=429, detail=str(error))
    background_tasks.add_task(data_source_service.run_sync_job, client_id, data_source_id, run["mode"], run["id"])
    return run

@app.get("/clients/me/data-sources/{data_source_id}/syncs", tags=["selfServiceClient"], response_model=List[SyncRun], summary="Recent sync runs", responses=_errors(401, 404, 422))
async def list_my_data_source_syncs(data_source_id: int, limit: int = Query(20, ge=1, le=100), supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Recent sync attempts for one data source - what the dashboard polls after "Synchroniser
    maintenant" to show progress, and reads today's runs from to grey the button out once the
    plan's daily cap is reached rather than let the click fail with a 429."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    try:
        return data_source_service.list_syncs(client_id, data_source_id, limit)
    except data_source_service.NotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))

@app.get("/clients/me/event-types", tags=["selfServiceClient"], response_model=List[EventType], summary="List my event types", responses=_errors(401, 404))
async def list_my_event_types(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Every event type this tenant has registered - "purchase"/"view" always exist
    (seeded at client creation, see seed_default_event_types in db.py); others appear
    either because the tenant added them here or because an integration auto-registered
    them by calling POST /events/{event_type} for a new type."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    return get_client_event_types(client_id)

@app.post("/clients/me/event-types", tags=["selfServiceClient"], response_model=EventType, summary="Register an event type", responses=_errors(401, 404, 422))
async def create_my_event_type(payload: EventTypeCreate, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Registers an event type for this account with a label and a training-weight tier (`aucun`, `faible`, `moyen`, `fort`). Types are also auto-registered the first time they are sent to `POST /events/{event_type}`."""
    if payload.tier not in EVENT_TIER_WEIGHTS:
        raise HTTPException(status_code=422, detail=f"Unknown tier '{payload.tier}' - must be one of {sorted(EVENT_TIER_WEIGHTS)}")
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    return upsert_client_event_type(client_id, payload.event_type, payload.label, payload.tier)

@app.patch("/clients/me/event-types/{event_type}", tags=["selfServiceClient"], response_model=EventType, summary="Update an event type", responses=_errors(401, 404, 422))
async def update_my_event_type(event_type: EventTypePath, payload: EventTypeUpdate, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Changes the label and/or tier of an existing event type. Past events keep their type; only future training uses the new weight."""
    if payload.tier is not None and payload.tier not in EVENT_TIER_WEIGHTS:
        raise HTTPException(status_code=422, detail=f"Unknown tier '{payload.tier}' - must be one of {sorted(EVENT_TIER_WEIGHTS)}")
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    existing = next((e for e in get_client_event_types(client_id) if e["event_type"] == event_type), None)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No event type '{event_type}' registered for this client")
    return upsert_client_event_type(
        client_id, event_type,
        payload.label if payload.label is not None else existing["label"],
        payload.tier if payload.tier is not None else existing["tier"],
    )

@app.delete("/clients/me/event-types/{event_type}", tags=["selfServiceClient"], response_model=Message, summary="Delete an event type", responses=_errors(401, 404, 422))
async def delete_my_event_type(event_type: EventTypePath, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Deleting the definition doesn't touch past interactions already recorded under
    this event_type - they fall back to the default tier's weight in training (see
    get_client_event_type_weights in db.py) rather than vanish or error."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    if not delete_client_event_type(client_id, event_type):
        raise HTTPException(status_code=404, detail=f"No event type '{event_type}' registered for this client")
    return {"message": f"Event type '{event_type}' deleted"}


@app.post("/clients/me/developer-keys", tags=["selfServiceClient"], response_model=DeveloperKeyCreated, summary="Create a developer key", responses=_errors(401, 404, 422))
async def create_my_developer_key(payload: DeveloperKeyCreate, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """A third credential kind, alongside the secret/public keys - meant for a coding agent
    (Claude Code, Codex) managing data sources, never for a runtime environment or browser
    JS. Scoped and independently revocable: a leak only exposes whatever `scopes` it was
    minted with (see GET /data-sources/types' sibling docs for the vocabulary), never full
    account access like the secret key. Shown once - store it now."""
    unknown = sorted(set(payload.scopes) - ALLOWED_SCOPES)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown scope(s) {unknown} - must be a subset of {sorted(ALLOWED_SCOPES)}")
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    row, raw_key = create_developer_key(client_id, payload.name, payload.scopes)
    return {**row, "key": raw_key}


@app.get("/clients/me/developer-keys", tags=["selfServiceClient"], response_model=List[DeveloperKey], summary="List my developer keys", responses=_errors(401, 404))
async def list_my_developer_keys(supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Never includes the raw key value - only create_my_developer_key does, once."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    return list_developer_keys(client_id)


@app.delete("/clients/me/developer-keys/{key_id}", tags=["selfServiceClient"], response_model=Message, summary="Revoke a developer key", responses=_errors(401, 404))
async def revoke_my_developer_key(key_id: int, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Immediate and permanent - a revoked key stops authenticating right away, with no
    replacement issued (mint a new one via create_my_developer_key if needed)."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    if not revoke_developer_key(client_id, key_id):
        raise HTTPException(status_code=404, detail=f"No developer key '{key_id}' for this account")
    return {"message": f"Developer key '{key_id}' revoked"}


def _clean_cell(row, column: str):
    """None for a missing/empty/NaN CSV cell - pandas represents an empty cell as NaN
    (a float), which every downstream Optional field here expects as None instead."""
    if column not in row.index:
        return None
    value = row[column]
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def _cell_id(row, *columns: str) -> Optional[str]:
    """First non-empty of the given columns, as a string. The CSV is read with every cell as
    text (see _read_import_file), so an id like "007" or "SKU-1" survives untouched instead of
    being coerced to a number."""
    for column in columns:
        value = _clean_cell(row, column)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _cell_int(row, column: str) -> Optional[int]:
    value = _clean_cell(row, column)
    return int(float(value)) if value is not None else None


def _cell_str(row, column: str) -> Optional[str]:
    value = _clean_cell(row, column)
    return str(value) if value is not None else None


def _json_records(raw: bytes) -> list[dict]:
    """The rows of a JSON import: a list of objects, or an object wrapping exactly one such list
    ({"items": [...]}, {"products": [...]}) - the shapes exports and APIs commonly produce."""
    data = json.loads(raw)
    if isinstance(data, dict):
        lists = [value for value in data.values() if isinstance(value, list)]
        if len(lists) != 1:
            raise ValueError("expected a list of objects, or an object holding exactly one list")
        data = lists[0]
    if not isinstance(data, list) or not all(isinstance(record, dict) for record in data):
        raise ValueError("expected a list of objects")
    return data


def _read_import_file(file: UploadFile) -> pd.DataFrame:
    """A CSV or JSON upload as a table of text cells with the same columns either way, so the
    three import routes below don't care which it was. `attrs["first_row"]` is the row number to
    report for the first record: 2 for a CSV (line 1 is the header), 1 for a JSON list."""
    is_json = (file.filename or "").lower().endswith(".json") or (file.content_type or "").split(";")[0] == "application/json"
    if is_json:
        try:
            records = _json_records(file.file.read())
            # Text like the CSV path: an id such as 1.0 or "007" must never become a number.
            frame = pd.DataFrame(
                [{key: (None if value is None else value if isinstance(value, str) else json.dumps(value)) for key, value in record.items()} for record in records],
                dtype=object,
            )
        except Exception as error:
            raise HTTPException(status_code=422, detail=f"Could not parse JSON: {error}")
        frame.attrs["first_row"] = 1
        return frame
    try:
        # dtype=str: ids are opaque strings now - never let pandas turn them into numbers.
        frame = pd.read_csv(file.file, dtype=str)
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"Could not parse CSV: {error}")
    frame.attrs["first_row"] = 2
    return frame


def _row_error_message(error: Exception) -> str:
    """What a CSV row's failure may say to the uploader. Validation problems (bad value,
    missing column, plan limit) are theirs to fix and safe to show; anything else - a database
    error carries the SQL statement and its parameters - is not, so it is logged and replaced."""
    from pydantic import ValidationError
    if isinstance(error, (ValueError, KeyError, PlanLimitError)) and not isinstance(error, ValidationError):
        return str(error)
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors())
    log_event("csv_row_failed", level=logging.ERROR, error_type=type(error).__name__)
    return "Unexpected error while importing this row"


def _import_summary(rows_total: int, errors: list[dict]) -> dict:
    return {"rows_total": rows_total, "rows_ok": rows_total - len(errors), "errors": errors}


@app.post(
    "/clients/me/import/products", tags=["selfServiceClient"], response_model=ImportSummary,
    summary="Import items from a CSV or JSON file",
    responses=_errors(401, 422),
)
async def import_my_products(
    data_product_type: ProductType, file: UploadFile = File(...),
    supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """Bulk equivalent of PUT /items/{item_id} - columns:
    item_id,title,description,genre_1,author,year,url,price (only item_id/title required;
    `work_id` is accepted as a deprecated name for `item_id`). A CSV file, or a `.json` file
    holding a list of objects with those same keys. A bad row is skipped and reported, not
    fatal to the whole import."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    df = _read_import_file(file)

    errors = []
    for i, row in df.iterrows():
        try:
            item_id = _cell_id(row, "item_id", "work_id")
            if item_id is None:
                raise ValueError("item_id is required")
            title = _cell_str(row, "title")
            if not title:
                raise ValueError("title is required")
            price = _clean_cell(row, "price")
            upsert_item(client_id, data_product_type, item_id, ItemUpsert(
                title=title, description=_cell_str(row, "description"),
                genre_1=_cell_str(row, "genre_1"), author=_cell_str(row, "author"),
                year=_cell_int(row, "year"), url=_cell_str(row, "url"),
                price=float(price) if price is not None else None,
            ))
        except Exception as error:
            errors.append({"row": i + df.attrs["first_row"], "message": _row_error_message(error)})

    return _import_summary(len(df), errors)


@app.post(
    "/clients/me/import/users", tags=["selfServiceClient"], response_model=ImportSummary,
    summary="Import users from a CSV or JSON file",
    responses=_errors(401, 422),
)
async def import_my_users(
    data_product_type: ProductType, file: UploadFile = File(...),
    supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """Bulk equivalent of PUT /users/{user_id} - columns:
    user_id,user_gender,user_age,user_zip,user_firstname,user_lastname (only user_id
    required - user profiles are optional enrichment, events work with bare user_ids
    alone)."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    df = _read_import_file(file)

    errors = []
    for i, row in df.iterrows():
        try:
            user_id = _cell_id(row, "user_id")
            if user_id is None:
                raise ValueError("user_id is required")
            upsert_user_profile(client_id, data_product_type, user_id, UserUpsert(
                user_gender=_cell_str(row, "user_gender"), user_age=_cell_int(row, "user_age"),
                user_zip=_cell_int(row, "user_zip"), user_firstname=_cell_str(row, "user_firstname"),
                user_lastname=_cell_str(row, "user_lastname"),
            ))
        except Exception as error:
            errors.append({"row": i + df.attrs["first_row"], "message": _row_error_message(error)})

    return _import_summary(len(df), errors)


@app.post(
    "/clients/me/import/interactions", tags=["selfServiceClient"], response_model=ImportSummary,
    summary="Import events from a CSV or JSON file",
    responses=_errors(401, 422),
)
async def import_my_interactions(
    data_product_type: ProductType, event_type: EventTypeQuery, background_tasks: BackgroundTasks,
    file: UploadFile = File(...), supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """Bulk equivalent of POST /events/{event_type} - columns:
    user_id,session_id,item_id,quantity,occurred_at,event_id (only item_id and one of
    user_id/session_id required; `work_id` is accepted as a deprecated name for `item_id`;
    quantity defaults to 1). Goes through the same record_events path as single-event
    ingestion, so it auto-registers a new event_type, honors event_id de-duplication, and can
    trigger an auto-retrain."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    df = _read_import_file(file)

    errors = []
    events = []
    for i, row in df.iterrows():
        try:
            occurred_at = _clean_cell(row, "occurred_at")
            quantity = _cell_int(row, "quantity")
            events.append((event_type, Event(
                user_id=_cell_id(row, "user_id"), session_id=_cell_id(row, "session_id"),
                item_id=_cell_id(row, "item_id", "work_id"), event_id=_cell_id(row, "event_id"),
                quantity=quantity if quantity is not None else 1,
                occurred_at=pd.to_datetime(occurred_at).to_pydatetime() if occurred_at is not None else None,
            )))
        except Exception as error:
            errors.append({"row": i + df.attrs["first_row"], "message": _row_error_message(error)})

    outcome = record_events(client_id, data_product_type, events)
    if outcome.carries_signal:
        maybe_trigger_auto_retrain(client_id, data_product_type, background_tasks)

    return _import_summary(len(df), errors)

@app.get("/admin/clients", tags=["admin"], response_model=List[ClientAdminView], summary="List all clients", responses=_errors(401, 403))
async def admin_list_clients(admin_user_id: str = Depends(get_current_admin_user_id)):
    """Operator-only: every client across the whole system, not scoped to the caller's
    own account - gated by app_metadata.is_admin, entirely separate from the
    self-service /clients/me* endpoints above."""
    return list_all_clients_with_usage()

@app.get("/admin/clients/{client_id}", tags=["admin"], response_model=ClientAdminView, summary="Get a client", responses=_errors(401, 403, 404))
async def admin_get_client(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Single-client detail (for the admin panel's side drawer) - same shape as one row
    of GET /admin/clients, without re-fetching the whole list."""
    row = get_client_admin_row(client_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return row

@app.patch("/admin/clients/{client_id}", tags=["admin"], response_model=ClientAdminView, summary="Rename a client or change its plan", responses=_errors(401, 403, 404, 422))
async def admin_update_client(client_id: int, payload: ClientRename, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Renames the client and/or changes its plan - contact_email is intentionally not
    editable here, since it's re-synced from the linked Supabase account's JWT on every
    login (see get_or_create_my_client) and a manual edit would just get silently
    overwritten."""
    if payload.plan is not None and payload.plan not in VALID_PLANS:
        raise HTTPException(status_code=422, detail=f"Unknown plan '{payload.plan}' - must be one of {sorted(VALID_PLANS)}")
    if get_client_admin_row(client_id) is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if payload.name is not None:
        rename_client(client_id, payload.name)
    if payload.plan is not None:
        set_client_plan(client_id, payload.plan)
    return get_client_admin_row(client_id)

@app.get("/admin/clients/{client_id}/usage", tags=["admin"], response_model=List[DailyUsage], summary="Client daily usage", responses=_errors(401, 403))
async def admin_client_usage(client_id: int, days: int = 30, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Requests per day for one client over the last `days` days (default 30)."""
    return get_client_usage_by_day(client_id, days)

@app.get("/admin/clients/{client_id}/models", tags=["admin"], response_model=List[ModelStatus], summary="Client models", responses=_errors(401, 403, 404))
async def admin_client_models(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """One entry per catalog (product_type) this client has pushed products for - the
    active model's training date and whether it came from a manual call or an automatic
    retrain, plus today's quota usage. Reuses build_model_status (see the self-service
    /clients/me/models/status route for the customer-facing equivalent)."""
    if get_client_admin_row(client_id) is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return [build_model_status(client_id, product_type) for product_type in list_product_types_for_client(client_id)]

@app.post("/admin/clients/{client_id}/disable", tags=["admin"], response_model=Message, summary="Disable a client", responses=_errors(401, 403, 404))
async def admin_disable_client(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Suspends a client: both its keys stop authenticating immediately (see
    get_client_and_scope_by_api_key's is_active check), without touching their data -
    reversible via /admin/clients/{client_id}/enable."""
    if not set_client_active(client_id, False):
        raise HTTPException(status_code=404, detail="Client not found")
    return {"message": "Client disabled"}

@app.post("/admin/clients/{client_id}/enable", tags=["admin"], response_model=Message, summary="Enable a client", responses=_errors(401, 403, 404))
async def admin_enable_client(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Re-enables a suspended client: both its keys authenticate again."""
    if not set_client_active(client_id, True):
        raise HTTPException(status_code=404, detail="Client not found")
    return {"message": "Client enabled"}

@app.post("/admin/clients/{client_id}/revoke-secret-key", tags=["admin"], response_model=Message, summary="Revoke a client's secret key", responses=_errors(401, 403))
async def admin_revoke_secret_key(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Kills the client's secret key immediately, with no replacement - use this for
    incident response (e.g. a leaked key), when the point is to cut access off right now
    and let the client self-serve a new one whenever they're ready. If the goal is
    instead to hand the client a working key over a support channel, use
    regenerate-secret-key below."""
    revoke_secret_key(client_id)
    return {"message": "Secret key revoked - the client must generate a new one from their account page"}

@app.post("/admin/clients/{client_id}/revoke-public-key", tags=["admin"], response_model=Message, summary="Revoke a client's public key", responses=_errors(401, 403))
async def admin_revoke_public_key(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Kills the client's public key immediately, with no replacement - the client must generate a new one from their account page."""
    revoke_public_key(client_id)
    return {"message": "Public key revoked - the client must generate a new one from their account page"}

@app.post("/admin/clients/{client_id}/regenerate-secret-key", tags=["admin"], response_model=ClientSelf, summary="Regenerate a client's secret key", responses=_errors(401, 403, 404))
async def admin_regenerate_secret_key(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Unlike revoke, this issues a replacement immediately and returns the raw value to
    the admin - a support workflow (a customer lost their key and needs it communicated
    back to them), not incident response. Prefer revoke for a leaked/compromised key."""
    if get_client_admin_row(client_id) is None:
        raise HTTPException(status_code=404, detail="Client not found")
    raw_key = regenerate_secret_key(client_id)
    row = get_client_admin_row(client_id)
    return {
        "client_id": client_id, "name": row["name"], "secret_key": raw_key,
        "has_public_key": row["has_public_key"],
        "secret_key_rotated_at": row["secret_key_rotated_at"],
        "public_key_rotated_at": row["public_key_rotated_at"],
    }

@app.post("/admin/clients/{client_id}/regenerate-public-key", tags=["admin"], response_model=ClientSelf, summary="Regenerate a client's public key", responses=_errors(401, 403, 404))
async def admin_regenerate_public_key(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Same as regenerate-secret-key, for the restricted public key."""
    if get_client_admin_row(client_id) is None:
        raise HTTPException(status_code=404, detail="Client not found")
    raw_key = regenerate_public_key(client_id)
    row = get_client_admin_row(client_id)
    return {
        "client_id": client_id, "name": row["name"], "public_key": raw_key,
        "has_public_key": True,
        "secret_key_rotated_at": row["secret_key_rotated_at"],
        "public_key_rotated_at": row["public_key_rotated_at"],
    }

@app.delete("/admin/clients/{client_id}", tags=["admin"], response_model=Message, summary="Delete a client permanently", responses=_errors(401, 403, 404))
async def admin_delete_client(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Permanently deletes a client and everything scoped to it - catalog, users,
    interactions, model version history, usage counters. There is no undo; the admin UI
    is expected to make this hard to trigger by accident (type-to-confirm), not this
    endpoint. The built-in demo client is protected since deleting it would break the
    public sales demo."""
    if client_id == DEMO_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Cannot delete the built-in demo client")
    if not delete_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    return {"message": "Client permanently deleted"}

# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

ItemIdPath = Annotated[str, Path(
    min_length=1, max_length=255,
    description=(
        "Your own item id - any string, e.g. `SKU-NIKE-001` or `gid://shopify/Product/123456` "
        "(URL-encode reserved characters). A legacy integer id is just its decimal string."
    ),
    examples=["SKU-NIKE-001"],
)]
UserIdPath = Annotated[str, Path(
    min_length=1, max_length=255,
    description="Your own user id - any string, e.g. `user_123` or a UUID. A legacy integer id is just its decimal string.",
    examples=["user_123"],
)]


@app.get(
    "/items", tags=["items"], response_model=List[Item], summary="List items",
    responses=_errors(401, 403, 422),
)
def items_list(
    response: Response, data_product_type: Catalog,
    limit: int = Query(100, ge=1, le=1000, description="Page size."),
    offset: int = Query(0, ge=0, description="Rows to skip."),
    client_id: int = Depends(get_current_client_id),
):
    """One page of the catalog, ordered by creation. The total number of items in this
    catalog is in the `X-Total-Count` response header. Secret key only: a public key lives in
    web pages, and must not be able to export your whole catalog (recommendations already
    return the few items they recommend)."""
    response.headers["X-Total-Count"] = str(count_products_in_catalog(client_id, data_product_type))
    return fetch_items(client_id, data_product_type, limit=limit, offset=offset)


@app.get(
    "/items/{item_id:path}", tags=["items"], response_model=Item, summary="Get an item",
    responses=_errors(401, 403, 404, 422),
)
def items_get(item_id: ItemIdPath, data_product_type: Catalog, client_id: int = Depends(get_current_client_id)):
    """One item by your own id. Secret key only."""
    items = fetch_items(client_id, data_product_type, item_id=item_id)
    if not items:
        raise HTTPException(status_code=404, detail=f"No item '{item_id}' in data_product_type={data_product_type}")
    return items[0]


@app.put(
    "/items/{item_id:path}", tags=["items"], response_model=Item, summary="Create or replace an item",
    responses={201: {"model": Item, "description": "The item did not exist and was created."}, **_errors(401, 403, 422)},
)
def items_upsert(
    item_id: ItemIdPath, payload: ItemUpsert, data_product_type: Catalog, response: Response,
    client_id: int = Depends(get_current_client_id),
):
    """Idempotent upsert: the body is the whole item, sent as often as you like. `201` when
    the item was created, `200` when an existing one was replaced. Only `title` is required;
    `description` and `properties.category` feed content-based similarity. Secret key only.
    Only brand-new items count against a free plan's item cap."""
    try:
        created = upsert_item(client_id, data_product_type, item_id, payload)
    except PlanLimitError as error:
        raise HTTPException(status_code=403, detail=str(error))
    if created:
        response.status_code = 201
    return fetch_items(client_id, data_product_type, item_id=item_id)[0]


@app.delete(
    "/items/{item_id:path}", tags=["items"], response_model=Message, summary="Delete an item",
    responses=_errors(401, 403, 404, 422),
)
def items_delete(item_id: ItemIdPath, data_product_type: Catalog, client_id: int = Depends(get_current_client_id)):
    """Removes the item from the catalog, so it is no longer recommended. Events already
    recorded for it are kept (they remain valid signal for other items' recommendations).
    Secret key only."""
    if delete_items(client_id, data_product_type, [item_id]):
        raise HTTPException(status_code=404, detail=f"No item '{item_id}' in data_product_type={data_product_type}")
    return {"message": f"Item '{item_id}' deleted from data_product_type={data_product_type}"}


@app.post(
    "/items/import", tags=["items"], response_model=BatchResult, summary="Upsert many items",
    responses=_errors(401, 403, 422),
)
def items_import(payload: ItemImportRequest, data_product_type: Catalog, client_id: int = Depends(get_current_client_id)):
    """Batch upsert of up to 1000 items in one call - each entry behaves exactly like
    `PUT /items/{item_id}`. An entry that fails (e.g. the free plan's item cap) is reported in
    `errors` without failing the others. For a spreadsheet, the account page has a CSV
    import. Secret key only."""
    outcome = upsert_items_batch(client_id, data_product_type, payload.items)
    log_event("items_import", client_id=client_id, product_type=data_product_type, received=outcome.received, succeeded=outcome.succeeded)
    return {"received": outcome.received, "succeeded": outcome.succeeded, "failed": len(outcome.errors), "errors": outcome.errors}


@app.post(
    "/items/delete", tags=["items"], response_model=BatchResult, summary="Delete many items",
    responses=_errors(401, 403, 422),
)
def items_delete_many(payload: ItemDeleteRequest, data_product_type: Catalog, client_id: int = Depends(get_current_client_id)):
    """Batch delete of up to 1000 items. Ids that don't exist are reported in `errors`; the
    rest are deleted. Secret key only."""
    missing = set(delete_items(client_id, data_product_type, payload.item_ids))
    errors = [
        {"index": i, "id": item_id, "message": "No such item"}
        for i, item_id in enumerate(payload.item_ids) if item_id in missing
    ]
    return {
        "received": len(payload.item_ids), "succeeded": len(payload.item_ids) - len(errors),
        "failed": len(errors), "errors": errors,
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@app.get(
    "/users", tags=["users"], response_model=List[User], summary="List users",
    responses=_errors(401, 403, 404, 422),
)
def users_list(
    response: Response, data_product_type: Catalog,
    user_id: Optional[str] = Query(None, deprecated=True, description="Deprecated: use GET /users/{user_id}."),
    count: Optional[int] = Query(None, deprecated=True, description="Deprecated alias of `limit`."),
    limit: Optional[int] = Query(None, ge=1, le=1000, description="Page size. Omit to get every user (historical behavior)."),
    offset: int = Query(0, ge=0),
    client_id: int = Depends(get_current_client_id),
):
    """User profiles for this catalog. The total is in the `X-Total-Count` response header.
    Secret key only. Each user has free-form `properties`; the historical `user_gender` /
    `user_age` / ... fields are still returned for accounts that used them (deprecated)."""
    users = fetch_users(client_id, data_product_type, user_id=user_id, limit=limit if limit is not None else count, offset=offset)
    if user_id is not None and not users:
        raise HTTPException(status_code=404, detail=f"This user ID {user_id} does not exist")
    response.headers["X-Total-Count"] = str(count_users_in_catalog(client_id, data_product_type))
    return users


@app.get(
    "/users/{user_id:path}", tags=["users"], response_model=User, summary="Get a user",
    responses=_errors(401, 403, 404, 422),
)
def users_get(user_id: UserIdPath, data_product_type: Catalog, client_id: int = Depends(get_current_client_id)):
    """One user profile by your own id. Secret key only."""
    users = fetch_users(client_id, data_product_type, user_id=user_id)
    if not users:
        raise HTTPException(status_code=404, detail=f"No user '{user_id}' in data_product_type={data_product_type}")
    return users[0]


@app.put(
    "/users/{user_id:path}", tags=["users"], response_model=User, summary="Create or replace a user",
    responses={201: {"model": User, "description": "The user did not exist and was created."}, **_errors(401, 403, 422)},
)
def users_upsert(
    user_id: UserIdPath, payload: UserUpsert, data_product_type: Catalog, response: Response,
    client_id: int = Depends(get_current_client_id),
):
    """Idempotent upsert of a user profile: `properties` is whatever describes your users
    (country, segment, age, ...) - free-form JSON. `201` when created, `200` when replaced.
    Creating a profile is optional: events referencing an unknown `user_id` work anyway.
    Secret key only."""
    created = upsert_user_profile(client_id, data_product_type, user_id, payload)
    if created:
        response.status_code = 201
    return fetch_users(client_id, data_product_type, user_id=user_id)[0]


@app.delete(
    "/users/{user_id:path}", tags=["users"], response_model=Message, summary="Delete a user",
    responses=_errors(401, 403, 404, 422),
)
def users_delete(user_id: UserIdPath, data_product_type: Catalog, client_id: int = Depends(get_current_client_id)):
    """Erases the user: their profile **and every event recorded for them** (right to
    erasure). The next model training no longer sees them. Secret key only."""
    if not delete_user(client_id, data_product_type, user_id):
        raise HTTPException(status_code=404, detail=f"No user '{user_id}' in data_product_type={data_product_type}")
    return {"message": f"User '{user_id}' deleted from data_product_type={data_product_type}"}


@app.post(
    "/users/import", tags=["users"], response_model=BatchResult, summary="Upsert many users",
    responses=_errors(401, 403, 422),
)
def users_import(payload: UserImportRequest, data_product_type: Catalog, client_id: int = Depends(get_current_client_id)):
    """Batch upsert of up to 1000 user profiles - each entry behaves like
    `PUT /users/{user_id}`. Secret key only."""
    outcome = upsert_users_batch(client_id, data_product_type, payload.users)
    log_event("users_import", client_id=client_id, product_type=data_product_type, received=outcome.received, succeeded=outcome.succeeded)
    return {"received": outcome.received, "succeeded": outcome.succeeded, "failed": len(outcome.errors), "errors": outcome.errors}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def _record_one(client_id: int, product_type: str, event_type: str, payload: Event, background_tasks: BackgroundTasks, label: str) -> EventResult:
    """The one path behind /events/purchase, /events/view and /events/{event_type}: the
    specialized routes are only URL shortcuts for the generic one, never a second
    implementation."""
    outcome = record_events(client_id, product_type, [(event_type, payload)])
    if outcome.carries_signal:
        maybe_trigger_auto_retrain(client_id, product_type, background_tasks)
    duplicate = outcome.duplicates > 0
    return EventResult(
        message=f"{label} event already recorded (duplicate event_id ignored)" if duplicate else f"{label} event recorded",
        event_id=payload.event_id, duplicate=duplicate,
    )


@app.post(
    "/events/purchase", tags=["events"], response_model=EventResult, summary="Track a purchase",
    responses=_errors(401, 422),
)
def events_purchase(
    data_product_type: Catalog, payload: Event, background_tasks: BackgroundTasks,
    client_id: int = Depends(get_current_client_id_public_ok),
):
    """Shortcut for `POST /events/{event_type}` with `event_type=purchase` - same body, same
    behavior. Put `price`, `currency`, `order_id`, `revenue` in `properties` and send an
    `event_id` (e.g. `evt_<order_id>_<item_id>`) so a retried request can't double-count the
    purchase. Accepts the public key."""
    return _record_one(client_id, data_product_type, PURCHASE, payload, background_tasks, "Purchase")


@app.post(
    "/events/view", tags=["events"], response_model=EventResult, summary="Track a view",
    responses=_errors(401, 422),
)
def events_view(
    data_product_type: Catalog, payload: Event, background_tasks: BackgroundTasks,
    client_id: int = Depends(get_current_client_id_public_ok),
):
    """Shortcut for `POST /events/{event_type}` with `event_type=view` - same body, same
    behavior. Works for an anonymous visitor: send `session_id` instead of `user_id`.
    Accepts the public key."""
    return _record_one(client_id, data_product_type, VIEW, payload, background_tasks, "View")


@app.post(
    "/events/identify", tags=["events"], response_model=IdentifyResult, summary="Link an anonymous session to a user",
    responses=_errors(401, 422),
)
def events_identify(payload: IdentifyRequest, data_product_type: Catalog, client_id: int = Depends(get_current_client_id_public_ok)):
    """Call this right after login/signup, once you know `user_id`, for the `session_id` the
    visitor was anonymous under: every past interaction of that session not already
    attributed to a user is reassigned to `user_id`, so it counts as that user's history from
    then on (collaborative/session recommendations, `get_placement_health`'s counts, ...).
    Safe to call more than once - only unattributed rows are ever touched, so it never
    reassigns a session's history away from a user it's already linked to. Declared before
    the catch-all `/events/{event_type}` - a literal path segment (like `/events/batch`)
    must be registered ahead of it or it would swallow this route instead. Accepts the public
    key - the same trust level as tracking the events themselves."""
    internal_user = resolve_internal_ids(client_id, data_product_type, KIND_USER, [payload.user_id], create=True)[payload.user_id]
    linked = link_session_to_user(client_id, data_product_type, payload.session_id, internal_user)
    log_event("events_identify", client_id=client_id, product_type=data_product_type, linked_interactions=linked)
    return {"linked_interactions": linked}


@app.post(
    "/events/batch", tags=["events"], response_model=EventBatchResult, summary="Track many events",
    responses=_errors(401, 422),
)
def events_batch(
    payload: EventBatch, data_product_type: Catalog, background_tasks: BackgroundTasks,
    client_id: int = Depends(get_current_client_id_public_ok),
):
    """Up to 1000 events in one request, each with its own `event_type` - for server-side
    tracking, imports, or a browser SDK flushing a queue. All-or-nothing validation (a
    malformed event gives a `422` naming its position and nothing is recorded); events whose
    `event_id` was already recorded are skipped and counted in `duplicates`. Accepts the public key."""
    outcome = record_events(client_id, data_product_type, [(e.event_type, e) for e in payload.events])
    if outcome.carries_signal:
        maybe_trigger_auto_retrain(client_id, data_product_type, background_tasks)
    log_event(
        "events_batch", client_id=client_id, product_type=data_product_type,
        received=len(payload.events), accepted=outcome.accepted, duplicates=outcome.duplicates,
    )
    return {"received": len(payload.events), "accepted": outcome.accepted, "duplicates": outcome.duplicates}


@app.post(
    "/events/{event_type}", tags=["events"], response_model=EventResult, summary="Track an event",
    responses=_errors(401, 422),
)
def events_track(
    event_type: EventTypePath, data_product_type: Catalog, payload: Event,
    background_tasks: BackgroundTasks, client_id: int = Depends(get_current_client_id_public_ok),
):
    """Records one interaction of any type. Officially supported types: `impression`,
    `view`, `click`, `add_to_cart`, `remove_from_cart`, `purchase` - any other string (a
    reservation, a watch, ...) is auto-registered on first use with the default weight, and
    can be retuned from the dashboard afterwards. No endpoint per event type is needed.

    - Identified user, anonymous visitor, or both: at least one of `user_id` / `session_id`.
    - Send the `recommendation_id` of the recommendation that surfaced the item to attribute impressions, clicks and purchases to it.
    - `impression` and `remove_from_cart` are recorded but carry no training weight.
    - `event_id` makes the call idempotent: a replay records nothing and returns `duplicate: true`.

    Accepts the public key."""
    return _record_one(client_id, data_product_type, event_type, payload, background_tasks, event_type)


def trigger_manual_training(client_id: int, product_type: str, background_tasks: BackgroundTasks) -> dict:
    """Shared by GET /generateModel (secret-key, for the customer's own backend) and
    POST /clients/me/generateModel (Supabase JWT, for the website) - identical quota
    check and job bookkeeping, only how client_id was resolved differs. The daily manual
    training count is a single account-wide counter regardless of which surface
    triggered it - otherwise a customer could double their free-tier allowance by using
    both surfaces."""
    manual_limit = get_plan_limits(get_client_plan(client_id))["manual_training_daily_limit"]
    if manual_limit is not None and count_trainings_today(client_id, product_type, MANUAL) >= manual_limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Free plan limit reached: {manual_limit} manual "
                "training run(s) per day. Try again tomorrow, or upgrade your plan for more."
            ),
        )

    job_id = str(uuid.uuid4())

    GENERATE_MODEL_JOBS[job_id] = {
        "job_id": job_id,
        "client_id": client_id,
        "status": "queued",
        "data_product_type": product_type,
        "detail": None,
        "version_id": None,
        "precision_at_k": None,
        "promoted": None,
    }
    background_tasks.add_task(run_generate_model_job, job_id, client_id, product_type, MANUAL)

    return GENERATE_MODEL_JOBS[job_id]


def get_job_or_404_for_client(job_id: str, client_id: int) -> dict:
    """Ownership check: a job_id is an unguessable UUID, but nothing previously stopped
    an authenticated client from reading another client's job status if they somehow
    learned/observed one - GENERATE_MODEL_JOBS now records client_id at creation time
    (see trigger_manual_training/maybe_trigger_auto_retrain) specifically so this can be
    enforced here."""
    job = GENERATE_MODEL_JOBS.get(job_id)
    if not job or job.get("client_id") != client_id:
        raise HTTPException(status_code=404, detail=f"Unknown job_id {job_id}")
    return job


def build_model_status(client_id: int, product_type: str) -> dict:
    """Shared by GET /models/status (secret-key, for the customer's own backend) and
    GET /clients/me/models/status (Supabase JWT, for the website) - same computation,
    two different ways of arriving at client_id. Everything a client dashboard needs to
    show "your model" in one call: the active version - including whether it came from a
    manual call or an automatic retrain, and its date - plus today's training quota
    usage, so a free-tier client can see clearly why a training run was refused or an
    auto-retrain skipped, instead of just noticing nothing changed."""
    limits = get_plan_limits(get_client_plan(client_id))
    skip = _auto_retrain_skips.get((client_id, product_type))
    skip_today = skip is not None and skip["skipped_at"].date() == utcnow().date()

    return {
        "data_product_type": product_type,
        "active_version": get_active_model_version(client_id, product_type),
        "manual_trainings_today": count_trainings_today(client_id, product_type, MANUAL),
        "manual_training_daily_limit": limits["manual_training_daily_limit"],
        "auto_retrains_today": count_trainings_today(client_id, product_type, AUTO),
        "auto_retrain_daily_limit": limits["auto_retrain_daily_limit"],
        "auto_retrain_skipped_today": skip_today,
        "auto_retrain_skip_reason": skip["reason"] if skip_today else None,
    }


async def _resolve_my_client_id(supabase_user_id: str, x_workspace_id: Optional[int] = None) -> int:
    """Which of this account's (possibly several) workspaces a self-service call is about.
    No X-Workspace-Id: the primary (oldest) workspace - today's only one, unchanged default
    for every dashboard call that predates multi-workspace. A given id is validated against
    the account's own owned workspaces (never trusted blindly) - the Supabase JWT carries no
    workspace hint of its own, so this header is the only signal, and it's checked every time."""
    owned = list_clients_by_supabase_user_id(supabase_user_id)
    if not owned:
        raise HTTPException(status_code=404, detail="No client for this account yet - call /clients/me first")
    if x_workspace_id is None:
        return owned[0]["id"]
    if not any(c["id"] == x_workspace_id for c in owned):
        raise HTTPException(status_code=403, detail="That workspace does not belong to this account")
    return x_workspace_id


@app.get("/generateModel", tags=["models"], response_model=GenerateModelJobStatus, summary="Train the model (background job)", responses=_errors(401, 403, 422))
async def generate_model(data_product_type: Catalog, background_tasks: BackgroundTasks, client_id: int = Depends(get_current_client_id)):
    """Starts a collaborative-model training run as a background job and returns its `job_id` immediately; poll `/generateModel/status/{job_id}`. Subject to the plan's daily manual-training quota (`429` when exhausted). The new model is only promoted to production if its precision@k is at least the current one's. Secret key only."""
    return trigger_manual_training(client_id, data_product_type, background_tasks)

@app.get("/generateModel/status/{job_id}", tags=["models"], response_model=GenerateModelJobStatus, summary="Training job status", responses=_errors(401, 403, 404))
async def generate_model_status(job_id: str, client_id: int = Depends(get_current_client_id)):
    """Status (`queued`, `running`, `completed`, `failed`) of a training job started by this account. Secret key only."""
    return get_job_or_404_for_client(job_id, client_id)

@app.get("/models/versions", tags=["models"], response_model=List[ModelVersion], summary="Model version history", responses=_errors(401, 403, 422))
async def get_model_versions(data_product_type: Catalog, client_id: int = Depends(get_current_client_id)):
    """History of trained ALS models for this client/product type - hyperparameters,
    precision@k, and which one is currently active (served)."""
    return list_model_versions(client_id, data_product_type)

@app.get("/models/status", tags=["models"], response_model=ModelStatus, summary="Model status and daily quota", responses=_errors(401, 403, 422))
async def get_model_status(data_product_type: Catalog, client_id: int = Depends(get_current_client_id)):
    """The active model version (when it was trained and whether manually or automatically) plus today's training quota usage. Secret key only."""
    return build_model_status(client_id, data_product_type)

@app.post("/clients/me/generateModel", tags=["selfServiceClient"], response_model=GenerateModelJobStatus, summary="Train my model", responses=_errors(401, 404, 422))
async def generate_my_model(
    data_product_type: ProductType, background_tasks: BackgroundTasks,
    supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None,
):
    """Self-service equivalent of GET /generateModel: the website only ever holds a
    Supabase session JWT, never the client's secret API key, so it can't call the
    secret-key-gated route directly - this lets a logged-in customer trigger a manual
    train from their own account page. Draws from the same daily quota as the secret-key
    route (see trigger_manual_training)."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    return trigger_manual_training(client_id, data_product_type, background_tasks)

@app.get("/clients/me/generateModel/status/{job_id}", tags=["selfServiceClient"], response_model=GenerateModelJobStatus, summary="My training job status", responses=_errors(401, 404))
async def generate_my_model_status(job_id: str, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Status of a training job started from the account page."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    return get_job_or_404_for_client(job_id, client_id)

@app.get("/clients/me/models/status", tags=["selfServiceClient"], response_model=ModelStatus, summary="My model status", responses=_errors(401, 404, 422))
async def get_my_model_status(data_product_type: ProductType, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Website equivalent of `GET /models/status`: the active model version and today's quota usage for one catalog."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    return build_model_status(client_id, data_product_type)

@app.get("/clients/me/models/versions", tags=["selfServiceClient"], response_model=List[ModelVersion], summary="My model versions", responses=_errors(401, 404, 422))
async def get_my_model_versions(data_product_type: ProductType, supabase_user_id: str = Depends(get_current_supabase_user_id), x_workspace_id: WorkspaceIdHeader = None):
    """Full training history for this catalog, not just the currently active version -
    self-service equivalent of the secret-key GET /models/versions, so a tenant can see
    from their own account when their model was created or last retrained."""
    client_id = await _resolve_my_client_id(supabase_user_id, x_workspace_id)
    return list_model_versions(client_id, data_product_type)

# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

recommendations_logger = get_logger()


def _store_recommendation_safe(**fields) -> None:
    """Runs after the response is sent: persisting the trace must never affect - or be able
    to fail - the recommendation call itself. A failure is logged and sent to Sentry; the
    only consequence is that events later carrying this recommendation_id can't be attributed."""
    try:
        store_recommendation(**fields)
    except Exception as error:
        sentry_sdk.capture_exception(error)
        log_event("recommendation_trace_failed", level=logging.ERROR, recommendation_id=fields.get("recommendation_id"), error=str(error))


def _finalize_recommendation(
    *, client_id: int, product_type: str, strategy: str, origin: str, presented: list[dict],
    background_tasks: BackgroundTasks, response: Response, user_id: Optional[str] = None,
    session_id: Optional[str] = None, item_id: Optional[str] = None, placement: Optional[str] = None,
    attempted: Optional[list[str]] = None,
) -> str:
    """Mints the recommendation_id, schedules the trace write, exposes the id in response
    headers (so the legacy array responses carry it too) and logs the call. Ids and counts
    only - no keys, no item content, no user properties."""
    recommendation_id = new_recommendation_id()
    item_ids = [item["item_id"] for item in presented]
    background_tasks.add_task(
        _store_recommendation_safe,
        recommendation_id=recommendation_id, client_id=client_id, product_type=product_type,
        strategy=strategy, origin=origin, item_ids=item_ids, user_id=user_id, session_id=session_id,
        item_id=item_id, placement=placement, request_id=request_id_var.get(),
    )
    response.headers["X-Recommendation-Id"] = recommendation_id
    response.headers["X-Recommendation-Strategy"] = strategy
    log_event(
        "recommendation", recommendation_id=recommendation_id, client_id=client_id,
        product_type=product_type, strategy=strategy, origin=origin, placement=placement,
        returned=len(item_ids), attempted=attempted,
        has_user=user_id is not None, has_session=session_id is not None, has_item=item_id is not None,
    )
    return recommendation_id


@app.post(
    "/getRec", tags=["recommendations"], response_model=RecommendationResponse,
    summary="Get recommendations",
    responses={
        200: {"description": "Always contains `recommendation_id` and `items` (possibly empty for an empty catalog)."},
        **_errors(401, 403, 422),
    },
)
def recommendations_get(
    payload: RecommendationRequest, data_product_type: Catalog, background_tasks: BackgroundTasks,
    response: Response, caller: Caller = Depends(get_caller_public_ok),
):
    """The recommendation endpoint to use. Send whatever you know about the moment - a
    `user_id`, an anonymous `session_id`, the `item_id` being viewed, the `viewed_item_ids` so
    far, a `placement` label - and LIKYLY chooses the strategy:

    | You send | Strategy |
    |---|---|
    | `user_id` + `item_id` | `hybrid` (personalized similar items) |
    | `item_id` | `content` (similar items) |
    | `viewed_item_ids` | `session` (recency-weighted from the viewed list) |
    | `user_id` (has history, model trained) | `collaborative` |
    | `user_id` or `session_id` with tracked views | `session` (from LIKYLY's own history of them) |
    | nothing / no data behind the signals | `popular` |

    Signals with no data behind them (an unknown item, a user with no events yet) are skipped
    - the request falls back down this list, ending at `popular`, and never fails for it. The
    `strategy` that actually produced the items is in the response. The `recommendation_id`
    is also in the `X-Recommendation-Id` header. Accepts the public key; `debug` needs the secret key."""
    if payload.debug and caller.scope != "secret":
        raise HTTPException(status_code=403, detail="debug requires the secret API key")

    result = recommend_auto(
        caller.client_id, data_product_type, user_id=payload.user_id, session_id=payload.session_id,
        item_id=payload.item_id, viewed_item_ids=payload.viewed_item_ids, count=payload.count,
    )
    items = present_records(caller.client_id, data_product_type, result.records, legacy=False, include_similar_users=payload.debug)
    recommendation_id = _finalize_recommendation(
        client_id=caller.client_id, product_type=data_product_type, strategy=result.strategy, origin="auto",
        presented=items, background_tasks=background_tasks, response=response, user_id=payload.user_id,
        session_id=payload.session_id, item_id=payload.item_id, placement=payload.placement,
        attempted=result.attempted,
    )
    return {"recommendation_id": recommendation_id, "strategy": result.strategy, "placement": payload.placement, "items": items}


# --- Strategy-specific endpoints (expert use; historical array response by default) --------

class RecTraceParams:
    """Query parameters shared by every strategy-specific endpoint: attribution context and
    the response shape switch."""

    def __init__(
        self,
        response_format: Literal["array", "object"] = Query(
            "array",
            description=(
                "`array` (default, **deprecated**): the historical bare list of items. `object`: "
                "`{recommendation_id, strategy, items}` - the same envelope as `POST /getRec`. The "
                "default will switch to `object` in a future version. Either way the "
                "recommendation_id is in the `X-Recommendation-Id` response header."
            ),
        ),
        placement: Optional[str] = Query(None, max_length=128, description="Free-form label of where these recommendations will be shown; stored with the recommendation for attribution.", examples=["product_page"]),
        session_id: Optional[str] = Query(None, max_length=255, description="Anonymous session these recommendations are for; stored with the recommendation for attribution."),
    ):
        self.response_format = response_format
        self.placement = placement
        self.session_id = session_id


RecCount = Annotated[int, Path(ge=1, le=500, description="How many items to return.")]

_REC_RESPONSES = {
    200: {"description": "A bare array of items by default, or `{recommendation_id, strategy, items}` with `response_format=object`."},
}


def _explicit_reply(
    records: list[dict], strategy: str, trace: RecTraceParams, *, client_id: int, product_type: str,
    background_tasks: BackgroundTasks, response: Response, user_id: Optional[str] = None,
    item_id: Optional[str] = None, secret_key: bool = False,
):
    legacy = trace.response_format == "array"
    # `similar_users` names OTHER users (first + last name): only ever for a secret-key caller.
    # A public key ships inside web pages - anyone can read it - so it must never see them.
    presented = present_records(client_id, product_type, records, legacy=legacy, include_similar_users=legacy and secret_key)
    recommendation_id = _finalize_recommendation(
        client_id=client_id, product_type=product_type, strategy=strategy, origin="explicit",
        presented=presented, background_tasks=background_tasks, response=response, user_id=user_id,
        session_id=trace.session_id, item_id=item_id, placement=trace.placement, attempted=[strategy],
    )
    if legacy:
        return presented
    return {"recommendation_id": recommendation_id, "strategy": strategy, "placement": trace.placement, "items": presented}


def _known_item_or_404(client_id: int, product_type: str, item_id: str) -> int:
    internal = resolve_internal_ids(client_id, product_type, KIND_ITEM, [item_id]).get(item_id)
    if internal is None or not product_exists(client_id, product_type, internal):
        raise HTTPException(status_code=404, detail=f"This product ID {item_id} does not exist")
    return internal


@app.get(
    "/getRec/popular/{count}", tags=["recommendations-advanced"],
    response_model=RecommendationResponse | List[RecommendedProduct],
    summary="Popular items", responses={**_REC_RESPONSES, **_errors(401, 422)},
)
async def get_rec_popular(
    data_product_type: Catalog, count: RecCount, background_tasks: BackgroundTasks, response: Response,
    trace: RecTraceParams = Depends(), client_id: int = Depends(get_current_client_id_public_ok),
):
    """Pure popularity ranking, no anchor item or user history needed - the true cold-start
    fallback for a visitor with nothing at all. Weighted by event type (a purchase counts
    more than a view; impressions don't count)."""
    records = rec_popular(client_id, data_product_type, count)
    return _explicit_reply(records, "popular", trace, client_id=client_id, product_type=data_product_type, background_tasks=background_tasks, response=response)


@app.get(
    "/getRec/content/{product_id}/{count}", tags=["recommendations-advanced"],
    response_model=RecommendationResponse | List[RecommendedProduct],
    summary="Items similar to an item", responses={**_REC_RESPONSES, **_errors(401, 404, 422)},
)
async def get_rec_content(
    data_product_type: Catalog, product_id: str, count: RecCount, background_tasks: BackgroundTasks,
    response: Response, trace: RecTraceParams = Depends(), client_id: int = Depends(get_current_client_id_public_ok),
):
    """Content-based filtering: items whose text (title, description, category, ...) and
    semantic embedding are closest to the given item. `product_id` is an `item_id` (the path
    segment can't contain `/` - use `POST /getRec` for ids that do)."""
    work_id = _known_item_or_404(client_id, data_product_type, product_id)
    records = rec_content(client_id, data_product_type, work_id, count)
    return _explicit_reply(records, "content", trace, client_id=client_id, product_type=data_product_type, background_tasks=background_tasks, response=response, item_id=product_id)


@app.get("/getRec/contentVec/createIndex", tags=["legacy"], response_model=Message, deprecated=True, summary="Create the Pinecone index (frozen legacy)", responses=_errors(401, 403, 422))
async def get_rec_content_vectordb_init(data_product_type: Catalog, client_id: int = Depends(get_current_client_id)):
    """**Frozen legacy.** Create a Pinecone index from product embeddings - superseded by
    pgvector-backed semantic similarity, already blended into `content` recommendations.
    Kept working for existing integrations, not recommended for new ones."""
    # List of works
    data_works = get_data(data_product_type, product_id=None, count=None, client_id=client_id)
    # create bag of words
    data_similarities = get_data_similarities(data_works)
    # get only bag of words and convert it to dictionnary
    data_similarities["id"] = data_similarities["work_id"].astype(str)
    # Transform list of works into dictionnary
    data_similarities_dict = data_similarities.to_dict(orient='records')
    data_similarities_prepared_for_vectors = data_similarities[["id", "bag_of_words"]].to_dict(orient='records')

    index, model, total_vectors = model_vector_indexing(
        data_similarities_dict,
        data_similarities_prepared_for_vectors,
        data_product_type,
        client_id=client_id,
    )

    return {"message": f"OK, Vector Index was created and total of {total_vectors} vectors were added to the index"}


@app.get("/getRec/contentVec/{product_id}/{count}", tags=["legacy"], response_model=List[VectorRecommendation], deprecated=True, summary="Similar items via Pinecone (frozen legacy)", responses=_errors(401, 404, 422))
async def get_rec_content_vectordb(data_product_type: Catalog, product_id: str, count: RecCount, client_id: int = Depends(get_current_client_id_public_ok)):
    """**Frozen legacy.** Content-based recommendations via a Pinecone vector index -
    superseded by `content`. Kept working for existing integrations."""
    work_id = _known_item_or_404(client_id, data_product_type, product_id)
    data_works = get_data(data_product_type, product_id=None, count=None, client_id=client_id)
    title = data_works.loc[data_works['work_id'] == work_id, 'title'].iloc[0]
    data_similarities = get_data_similarities(data_works)
    data_similarities["id"] = data_similarities["work_id"].astype(str)
    data_similarities_dict = data_similarities.to_dict(orient='records')
    data_similarities_prepared_for_vectors = data_similarities[["id", "bag_of_words"]].to_dict(orient='records')

    matches = model_content_recommender_vectors(
        data_similarities_dict, data_similarities_prepared_for_vectors, title, count,
        data_product_type, client_id=client_id,
    )
    # The Pinecone ids are the engine's internal ints (as strings) - translate to the public ids.
    external = resolve_external_ids(client_id, data_product_type, KIND_ITEM, [int(m["work_id"]) for m in matches])
    out = []
    for match in matches:
        item_id = external.get(int(match["work_id"]), match["work_id"])
        out.append({"item_id": item_id, "work_id": item_id, "title": match["title"]})
    return out


@app.get(
    "/getRec/collaborative/{user_id}/{count}", tags=["recommendations-advanced"],
    response_model=RecommendationResponse | List[RecommendedProduct],
    summary="Items liked by similar users", responses={**_REC_RESPONSES, **_errors(401, 404, 422)},
)
async def get_rec_collaborative(
    data_product_type: Catalog, user_id: str, count: RecCount, background_tasks: BackgroundTasks,
    response: Response, trace: RecTraceParams = Depends(), caller: Caller = Depends(get_caller_public_ok),
):
    """User-based collaborative filtering (implicit ALS): items appreciated by users with
    similar histories, excluding what this user already bought. Needs a trained model
    (`/generateModel`) - `404` until then. With the secret key, the legacy array response's
    explanation includes `similar_users` (other users' names); with the public key, or with
    `response_format=object`, it never does."""
    client_id = caller.client_id
    internal_user = resolve_internal_ids(client_id, data_product_type, KIND_USER, [user_id]).get(user_id)
    if internal_user is None:
        raise HTTPException(status_code=404, detail=f"Unknown user_id '{user_id}'")
    try:
        records = rec_collaborative(client_id, data_product_type, internal_user, count)
    except FileNotFoundError:
        # (the exception text is a server file path - never echoed)
        raise HTTPException(status_code=404, detail="No trained model for this catalog yet - call /generateModel first")
    except Exception as error:
        sentry_sdk.capture_exception(error)
        log_event("collaborative_failed", level=logging.ERROR, client_id=client_id, error_type=type(error).__name__)
        raise HTTPException(status_code=500, detail="Collaborative recommendation failed")
    return _explicit_reply(records, "collaborative", trace, client_id=client_id, product_type=data_product_type, background_tasks=background_tasks, response=response, user_id=user_id, secret_key=caller.scope == "secret")


@app.get(
    "/getRec/hybrid/{user_id}/{product_id}/{count}", tags=["recommendations-advanced"],
    response_model=RecommendationResponse | List[RecommendedProduct],
    summary="Personalized similar items (hybrid)", responses={**_REC_RESPONSES, **_errors(401, 404, 422)},
)
async def get_rec_hybrid(
    data_product_type: Catalog, user_id: str, product_id: str, count: RecCount,
    background_tasks: BackgroundTasks, response: Response, trace: RecTraceParams = Depends(),
    alpha: float = Query(0.5, ge=0, le=1, description="Weight of the collaborative signal against content similarity (0 = pure content, 1 = pure collaborative). An expert knob: `POST /getRec` picks a sensible blend for you."),
    client_id: int = Depends(get_current_client_id_public_ok),
):
    """Blends content similarity to `product_id` with the user's collaborative signal:
    `score = alpha * collaborative + (1 - alpha) * content`. Degrades to pure content ranking
    when no model is trained or the user is unknown."""
    work_id = _known_item_or_404(client_id, data_product_type, product_id)
    internal_user = resolve_internal_ids(client_id, data_product_type, KIND_USER, [user_id]).get(user_id)
    records = rec_hybrid(client_id, data_product_type, internal_user, work_id, count, alpha=alpha)
    return _explicit_reply(records, "hybrid", trace, client_id=client_id, product_type=data_product_type, background_tasks=background_tasks, response=response, user_id=user_id, item_id=product_id)


@app.get(
    "/getRec/session", tags=["recommendations-advanced"],
    response_model=RecommendationResponse | List[RecommendedProduct],
    summary="Recommendations from a viewed list", responses={**_REC_RESPONSES, **_errors(401, 404, 422)},
)
async def get_rec_session(
    data_product_type: Catalog, background_tasks: BackgroundTasks, response: Response,
    viewed_item_ids: Optional[str] = Query(None, description="Comma-separated `item_id`s, oldest first. Ids containing a comma need `POST /getRec`.", examples=["item_123,item_456"]),
    viewed_work_ids: Optional[str] = Query(None, deprecated=True, description="Deprecated alias of `viewed_item_ids`."),
    count: int = Query(3, ge=1, le=500), trace: RecTraceParams = Depends(),
    client_id: int = Depends(get_current_client_id_public_ok),
):
    """Recency-weighted recommendations from a list of recently viewed items - no account or
    login needed. The caller keeps the list (browser storage) and sends it on each call; the
    API stays stateless."""
    raw = viewed_item_ids if viewed_item_ids is not None else viewed_work_ids
    if raw is None:
        raise HTTPException(status_code=422, detail="viewed_item_ids is required")
    viewed = [x.strip() for x in raw.split(',') if x.strip()]
    if not viewed:
        raise HTTPException(status_code=422, detail="viewed_item_ids must contain at least one item_id")

    mapping = resolve_internal_ids(client_id, data_product_type, KIND_ITEM, viewed)
    viewed_internal = [mapping[v] for v in viewed if v in mapping]
    records = rec_session(client_id, data_product_type, viewed_internal, count)
    if not records:
        raise HTTPException(status_code=404, detail="None of the given viewed_item_ids exist in the catalog")
    return _explicit_reply(records, "session", trace, client_id=client_id, product_type=data_product_type, background_tasks=background_tasks, response=response)


@app.get(
    "/getRec/sessionForUser/{user_id}/{count}", tags=["recommendations-advanced"],
    response_model=RecommendationResponse | List[RecommendedProduct],
    summary="Recommendations from a user's tracked views", responses={**_REC_RESPONSES, **_errors(401, 422)},
)
async def get_rec_session_for_user(
    data_product_type: Catalog, user_id: str, count: RecCount, background_tasks: BackgroundTasks,
    response: Response, trace: RecTraceParams = Depends(), client_id: int = Depends(get_current_client_id_public_ok),
):
    """Same recency-weighted recs as `/getRec/session`, but the recently-viewed list comes
    from the views tracked for this user (persisted history) instead of a client-supplied
    list - so "for you" recommendations survive across devices and sessions. Empty when the
    user has no tracked views."""
    internal_user = resolve_internal_ids(client_id, data_product_type, KIND_USER, [user_id]).get(user_id)
    viewed = get_recent_viewed_work_ids(client_id, data_product_type, internal_user, limit=10) if internal_user is not None else []
    records = rec_session(client_id, data_product_type, viewed, count) if viewed else []
    return _explicit_reply(records, "session", trace, client_id=client_id, product_type=data_product_type, background_tasks=background_tasks, response=response, user_id=user_id)

origins = ['*']

# allow_credentials=True is invalid together with a wildcard origin (browsers refuse the
# combination per spec) - and it was never needed anyway, since every client authenticates
# via the X-API-Key header, not cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Browsers hide every non-safelisted response header from page JS unless it is exposed
    # here - and the legacy array responses deliver their recommendation_id in a header.
    expose_headers=["X-Request-ID", "X-Recommendation-Id", "X-Recommendation-Strategy", "X-Total-Count"],
)

# Prometheus metrics: request count and latency, labeled by the route *template* (e.g.
# "/getRec/content/{product_id}/{count}") rather than the raw URL, so a busy endpoint's
# metrics don't explode into one time series per product_id ever requested.
REQUEST_COUNT = Counter(
    "recsys_api_requests_total", "Total API requests", ["method", "path", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "recsys_api_request_duration_seconds", "Request latency in seconds", ["method", "path"],
)


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    route = request.scope.get("route")
    path = route.path if route is not None else request.url.path
    REQUEST_COUNT.labels(method=request.method, path=path, status_code=response.status_code).inc()
    REQUEST_LATENCY.labels(method=request.method, path=path).observe(duration)
    return response


@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Scraped by Prometheus - not client-facing, so no X-API-Key auth (a scraper has no
    client identity to authenticate as). Restrict actual network access to this path at
    the reverse-proxy/firewall level in production, not here."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Outermost middleware (registered last): gives every request an id before anything
    else runs, so every log line and error body produced while handling it can carry it.
    Deliberately reads no credentials and logs no query string or body."""
    request_id = coerce_request_id(request.headers.get("x-request-id"))
    request.state.request_id = request_id
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id

    route = request.scope.get("route")
    log_event(
        "request", request_id=request_id, method=request.method,
        path=getattr(route, "path_format", None) or (route.path if route is not None else request.url.path),
        status=response.status_code, duration_ms=round((time.perf_counter() - start) * 1000, 1),
    )
    return response


def _current_request_id(request: Request) -> Optional[str]:
    return getattr(request.state, "request_id", None) or request_id_var.get()


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        {"detail": exc.detail, "request_id": _current_request_id(request)},
        status_code=exc.status_code, headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    from fastapi.encoders import jsonable_encoder
    return JSONResponse(
        {"detail": jsonable_encoder(exc.errors()), "request_id": _current_request_id(request)},
        status_code=422,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = _current_request_id(request)
    get_logger().error(
        "unhandled_exception",
        extra={"fields": {"event": "unhandled_exception", "path": request.url.path, "method": request.method}},
        exc_info=exc,
    )
    return JSONResponse(
        {"detail": "Internal server error - quote this request_id when reporting it", "request_id": request_id},
        status_code=500,
        # This response is built by Starlette's outermost error middleware, which sits outside
        # request_id_middleware - so the header has to be set here.
        headers={"X-Request-ID": request_id} if request_id else None,
    )


def _required_key_by_operation() -> dict[tuple[str, str], str]:
    """(path, method) -> "secret" | "public", read off each route's actual auth dependency -
    so the OpenAPI states what the code enforces instead of what a docstring claims."""
    from fastapi.routing import APIRoute

    def dependency_calls(dependant):
        for sub in dependant.dependencies:
            yield sub.call
            yield from dependency_calls(sub)

    required: dict[tuple[str, str], str] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        calls = set(dependency_calls(route.dependant))
        if get_current_client_id in calls:
            level = "secret"
        elif get_current_client_id_public_ok in calls or get_caller_public_ok in calls:
            level = "public"
        else:
            # The /data-sources* and /placements* routers' scope-based dependency
            # (scoped_auth.require_scope/any_valid_key/public_ok_client_id) isn't one fixed
            # function - a new closure is created per route, so it can't be matched by identity
            # like the two checks above. It marks itself with `likyly_required_scope` instead;
            # read it off whichever dependency in this route's tree carries it.
            scope = next((getattr(call, "likyly_required_scope", None) for call in calls if getattr(call, "likyly_required_scope", None)), None)
            if not scope:
                continue
            # "public_ok" behaves exactly like the existing "public" vocabulary (secret-or-
            # public, no scope to lack, so no 403 for missing one) - map it there rather than
            # inventing a value the secret/public two-key model didn't already have.
            if scope == "public_ok":
                level = "public"
            elif scope == "any":
                level = "any"
            else:
                level = f"scope:{scope}"
        for method in route.methods:
            required[(route.path_format, method.lower())] = level
    return required


_base_openapi = app.openapi


def custom_openapi():
    """FastAPI's schema plus what it can't infer: the Supabase bearer scheme on the
    account/admin routes, and an `x-required-key` on each API-key route (`secret` = secret
    key only, `public` = either key, `scope:<name>` = the secret key or a developer key
    carrying that scope - see /data-sources*, `any` = any of the three key kinds, no
    specific scope - see GET /data-sources/types)."""
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = _base_openapi()
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["bearerAuth"] = {
        "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
        "description": "Supabase session JWT of the logged-in website user (account and admin routes only - not an API key).",
    }
    required = _required_key_by_operation()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if path.startswith("/clients/me") or path.startswith("/admin"):
                operation["security"] = [{"bearerAuth": []}]
            level = required.get((path, method))
            if level:
                operation["x-required-key"] = level
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]
if __name__ == "__main__":
    # Pass the app object directly, not the "app:app" string form: the string form makes
    # uvicorn re-import this module by path, and since this file is already running as
    # __main__, that re-import executes everything in it a second time in the same
    # process - harmless for route definitions, but fatal for prometheus_client's Counter/
    # Histogram above, which register into a single process-wide registry and raise on a
    # second registration of the same metric name.
    uvicorn.run(app, host='0.0.0.0', port=6061)
