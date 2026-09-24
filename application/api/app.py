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
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from dataclasses import dataclass
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
    ClientAdminView, DailyUsage, ClientRename, ClientUsageSummary,
    EventType, EventTypeCreate, EventTypeUpdate, ImportSummary, Item, ItemUpsert, ItemImportRequest, ItemDeleteRequest, BatchResult, ErrorResponse,
    UserUpsert, UserImportRequest, Event, EventBatch, EventResult, EventBatchResult,
    RecommendationRequest, RecommendationResponse,
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
    get_client_by_supabase_user_id, create_client_for_supabase_user, set_client_contact_email,
    regenerate_secret_key, regenerate_public_key, revoke_secret_key, revoke_public_key,
    set_client_active, delete_client, get_client_admin_row, rename_client,
    touch_client_usage, list_all_clients_with_usage, get_client_usage_by_day,
    get_active_model_version, count_products_for_client, count_products_in_catalog,
    count_users_in_catalog, count_trainings_today,
    list_product_types_for_client, get_client_plan, set_client_plan,
    get_client_event_types, upsert_client_event_type, delete_client_event_type, list_catalogs_for_client,
    EVENT_TIER_WEIGHTS, MANUAL, AUTO, VALID_PLANS, get_plan_limits, utcnow,
    KIND_ITEM, KIND_USER, resolve_internal_ids, resolve_external_ids, product_exists,
    get_recent_viewed_work_ids,
    store_recommendation,
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
    "3. `POST /getRec` - ask for recommendations. You get a `recommendation_id`; send it back "
    "on the `impression` / `click` / `add_to_cart` / `purchase` events to attribute them.\n\n"
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
              )


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
        name=f"Self-service client {supabase_user_id}", supabase_user_id=supabase_user_id, email=email,
    )
    created = get_client_by_supabase_user_id(supabase_user_id)
    return {
        "client_id": client_id, "name": created["name"],
        "secret_key": raw_secret_key, "public_key": raw_public_key,
        "has_public_key": created["has_public_key"],
        "secret_key_rotated_at": created["secret_key_rotated_at"],
        "public_key_rotated_at": created["public_key_rotated_at"],
        "product_types": [],
    }

@app.post("/clients/me/regenerate-secret-key", tags=["selfServiceClient"], response_model=ClientSelf, summary="Regenerate my secret key", responses=_errors(401, 404))
async def regenerate_my_secret_key(supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Invalidates the current secret key and issues a new one - the only way to recover
    from a lost key, since the raw value is never stored."""
    existing = get_client_by_supabase_user_id(supabase_user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="No client for this account yet - call /clients/me first")

    raw_key = regenerate_secret_key(existing["id"])
    updated = get_client_by_supabase_user_id(supabase_user_id)
    return {
        "client_id": existing["id"], "name": existing["name"], "secret_key": raw_key,
        "has_public_key": updated["has_public_key"],
        "secret_key_rotated_at": updated["secret_key_rotated_at"],
        "public_key_rotated_at": updated["public_key_rotated_at"],
        "product_types": list_product_types_for_client(existing["id"]),
    }

@app.post("/clients/me/regenerate-public-key", tags=["selfServiceClient"], response_model=ClientSelf, summary="Regenerate my public key", responses=_errors(401, 404))
async def regenerate_my_public_key(supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Same as regenerate-secret-key, for the restricted public key."""
    existing = get_client_by_supabase_user_id(supabase_user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="No client for this account yet - call /clients/me first")

    raw_key = regenerate_public_key(existing["id"])
    updated = get_client_by_supabase_user_id(supabase_user_id)
    return {
        "client_id": existing["id"], "name": existing["name"], "public_key": raw_key,
        "has_public_key": True,
        "secret_key_rotated_at": updated["secret_key_rotated_at"],
        "public_key_rotated_at": updated["public_key_rotated_at"],
        "product_types": list_product_types_for_client(existing["id"]),
    }

@app.get("/clients/me/usage", tags=["selfServiceClient"], response_model=ClientUsageSummary, summary="My plan usage", responses=_errors(401, 404))
async def get_my_usage(supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Account-wide plan/product-count numbers for the self-service dashboard - separate
    from /clients/me/models/status, which is scoped to one product_type."""
    existing = get_client_by_supabase_user_id(supabase_user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="No client for this account yet - call /clients/me first")

    client_id = existing["id"]
    plan = get_client_plan(client_id)
    return {
        "plan": plan,
        "product_count": count_products_for_client(client_id),
        "product_limit": get_plan_limits(plan)["product_limit"],
    }

@app.get("/clients/me/event-types", tags=["selfServiceClient"], response_model=List[EventType], summary="List my event types", responses=_errors(401, 404))
async def list_my_event_types(supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Every event type this tenant has registered - "purchase"/"view" always exist
    (seeded at client creation, see seed_default_event_types in db.py); others appear
    either because the tenant added them here or because an integration auto-registered
    them by calling POST /events/{event_type} for a new type."""
    client_id = await _resolve_my_client_id(supabase_user_id)
    return get_client_event_types(client_id)

@app.post("/clients/me/event-types", tags=["selfServiceClient"], response_model=EventType, summary="Register an event type", responses=_errors(401, 404, 422))
async def create_my_event_type(payload: EventTypeCreate, supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Registers an event type for this account with a label and a training-weight tier (`aucun`, `faible`, `moyen`, `fort`). Types are also auto-registered the first time they are sent to `POST /events/{event_type}`."""
    if payload.tier not in EVENT_TIER_WEIGHTS:
        raise HTTPException(status_code=422, detail=f"Unknown tier '{payload.tier}' - must be one of {sorted(EVENT_TIER_WEIGHTS)}")
    client_id = await _resolve_my_client_id(supabase_user_id)
    return upsert_client_event_type(client_id, payload.event_type, payload.label, payload.tier)

@app.patch("/clients/me/event-types/{event_type}", tags=["selfServiceClient"], response_model=EventType, summary="Update an event type", responses=_errors(401, 404, 422))
async def update_my_event_type(event_type: EventTypePath, payload: EventTypeUpdate, supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Changes the label and/or tier of an existing event type. Past events keep their type; only future training uses the new weight."""
    if payload.tier is not None and payload.tier not in EVENT_TIER_WEIGHTS:
        raise HTTPException(status_code=422, detail=f"Unknown tier '{payload.tier}' - must be one of {sorted(EVENT_TIER_WEIGHTS)}")
    client_id = await _resolve_my_client_id(supabase_user_id)
    existing = next((e for e in get_client_event_types(client_id) if e["event_type"] == event_type), None)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No event type '{event_type}' registered for this client")
    return upsert_client_event_type(
        client_id, event_type,
        payload.label if payload.label is not None else existing["label"],
        payload.tier if payload.tier is not None else existing["tier"],
    )

@app.delete("/clients/me/event-types/{event_type}", tags=["selfServiceClient"], response_model=Message, summary="Delete an event type", responses=_errors(401, 404, 422))
async def delete_my_event_type(event_type: EventTypePath, supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Deleting the definition doesn't touch past interactions already recorded under
    this event_type - they fall back to the default tier's weight in training (see
    get_client_event_type_weights in db.py) rather than vanish or error."""
    client_id = await _resolve_my_client_id(supabase_user_id)
    if not delete_client_event_type(client_id, event_type):
        raise HTTPException(status_code=404, detail=f"No event type '{event_type}' registered for this client")
    return {"message": f"Event type '{event_type}' deleted"}


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
    text (see _read_import_csv), so an id like "007" or "SKU-1" survives untouched instead of
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


def _read_import_csv(file: UploadFile) -> pd.DataFrame:
    try:
        # dtype=str: ids are opaque strings now - never let pandas turn them into numbers.
        return pd.read_csv(file.file, dtype=str)
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"Could not parse CSV: {error}")


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
    summary="Import items from a CSV file",
    responses=_errors(401, 422),
)
async def import_my_products(
    data_product_type: ProductType, file: UploadFile = File(...),
    supabase_user_id: str = Depends(get_current_supabase_user_id),
):
    """Bulk equivalent of PUT /items/{item_id} - columns:
    item_id,title,description,genre_1,author,year,url,price (only item_id/title required;
    `work_id` is accepted as a deprecated name for `item_id`). A bad row is skipped and
    reported, not fatal to the whole import."""
    client_id = await _resolve_my_client_id(supabase_user_id)
    df = _read_import_csv(file)

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
            errors.append({"row": i + 2, "message": _row_error_message(error)})

    return _import_summary(len(df), errors)


@app.post(
    "/clients/me/import/users", tags=["selfServiceClient"], response_model=ImportSummary,
    summary="Import users from a CSV file",
    responses=_errors(401, 422),
)
async def import_my_users(
    data_product_type: ProductType, file: UploadFile = File(...),
    supabase_user_id: str = Depends(get_current_supabase_user_id),
):
    """Bulk equivalent of PUT /users/{user_id} - columns:
    user_id,user_gender,user_age,user_zip,user_firstname,user_lastname (only user_id
    required - user profiles are optional enrichment, events work with bare user_ids
    alone)."""
    client_id = await _resolve_my_client_id(supabase_user_id)
    df = _read_import_csv(file)

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
            errors.append({"row": i + 2, "message": _row_error_message(error)})

    return _import_summary(len(df), errors)


@app.post(
    "/clients/me/import/interactions", tags=["selfServiceClient"], response_model=ImportSummary,
    summary="Import events from a CSV file",
    responses=_errors(401, 422),
)
async def import_my_interactions(
    data_product_type: ProductType, event_type: EventTypeQuery, background_tasks: BackgroundTasks,
    file: UploadFile = File(...), supabase_user_id: str = Depends(get_current_supabase_user_id),
):
    """Bulk equivalent of POST /events/{event_type} - columns:
    user_id,session_id,item_id,quantity,occurred_at,event_id (only item_id and one of
    user_id/session_id required; `work_id` is accepted as a deprecated name for `item_id`;
    quantity defaults to 1). Goes through the same record_events path as single-event
    ingestion, so it auto-registers a new event_type, honors event_id de-duplication, and can
    trigger an auto-retrain."""
    client_id = await _resolve_my_client_id(supabase_user_id)
    df = _read_import_csv(file)

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
            errors.append({"row": i + 2, "message": _row_error_message(error)})

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


async def _resolve_my_client_id(supabase_user_id: str) -> int:
    existing = get_client_by_supabase_user_id(supabase_user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="No client for this account yet - call /clients/me first")
    return existing["id"]


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
    supabase_user_id: str = Depends(get_current_supabase_user_id),
):
    """Self-service equivalent of GET /generateModel: the website only ever holds a
    Supabase session JWT, never the client's secret API key, so it can't call the
    secret-key-gated route directly - this lets a logged-in customer trigger a manual
    train from their own account page. Draws from the same daily quota as the secret-key
    route (see trigger_manual_training)."""
    client_id = await _resolve_my_client_id(supabase_user_id)
    return trigger_manual_training(client_id, data_product_type, background_tasks)

@app.get("/clients/me/generateModel/status/{job_id}", tags=["selfServiceClient"], response_model=GenerateModelJobStatus, summary="My training job status", responses=_errors(401, 404))
async def generate_my_model_status(job_id: str, supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Status of a training job started from the account page."""
    client_id = await _resolve_my_client_id(supabase_user_id)
    return get_job_or_404_for_client(job_id, client_id)

@app.get("/clients/me/models/status", tags=["selfServiceClient"], response_model=ModelStatus, summary="My model status", responses=_errors(401, 404, 422))
async def get_my_model_status(data_product_type: ProductType, supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Website equivalent of `GET /models/status`: the active model version and today's quota usage for one catalog."""
    client_id = await _resolve_my_client_id(supabase_user_id)
    return build_model_status(client_id, data_product_type)

@app.get("/clients/me/models/versions", tags=["selfServiceClient"], response_model=List[ModelVersion], summary="My model versions", responses=_errors(401, 404, 422))
async def get_my_model_versions(data_product_type: ProductType, supabase_user_id: str = Depends(get_current_supabase_user_id)):
    """Full training history for this catalog, not just the currently active version -
    self-service equivalent of the secret-key GET /models/versions, so a tenant can see
    from their own account when their model was created or last retrained."""
    client_id = await _resolve_my_client_id(supabase_user_id)
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
            continue
        for method in route.methods:
            required[(route.path_format, method.lower())] = level
    return required


_base_openapi = app.openapi


def custom_openapi():
    """FastAPI's schema plus what it can't infer: the Supabase bearer scheme on the
    account/admin routes, and an `x-required-key` on each API-key route (`secret` = secret
    key only, `public` = either key)."""
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
