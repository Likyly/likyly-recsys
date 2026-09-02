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
    )

from fastapi import FastAPI, Request, Form, HTTPException, BackgroundTasks, Security, Depends, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi.responses import Response
from typing import Optional, Annotated
from fastapi.templating import Jinja2Templates
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import json
import math
import time
import uuid
import jwt
from jwt import PyJWKClient

from pydantic import BaseModel, Field, Json
from typing import Any
from typing import List

import sys
import uvicorn
import numpy as np
import pandas as pd

from schemas import (
    Product, RecommendedProduct, VectorRecommendation, User, Purchase,
    Rating, PageView, Message, GenerateModelJobStatus, ModelVersion, ModelStatus,
    ProductProfileUpsert, PurchaseEvent, ViewEvent, ClientSelf,
    ClientAdminView, DailyUsage, ClientRename,
)


current_dir = os.path.dirname(os.path.abspath(__file__))
relative_path_utils = "../utils"
absolute_path_utils = os.path.abspath(os.path.join(current_dir, relative_path_utils))
sys.path.insert(0, absolute_path_utils)

#print(sys.path)

from exploreData import *
from modelData import *
from db import (
    upsert_product_profile, delete_product_profile, insert_interaction, init_db,
    get_client_and_scope_by_api_key, ensure_client_id, DEMO_CLIENT_ID, PURCHASE, VIEW,
    list_model_versions, count_interactions_since, get_recent_viewed_work_ids,
    get_client_by_supabase_user_id, create_client_for_supabase_user, set_client_contact_email,
    regenerate_secret_key, regenerate_public_key, revoke_secret_key, revoke_public_key,
    set_client_active, delete_client, get_client_admin_row, rename_client,
    touch_client_usage, list_all_clients_with_usage, get_client_usage_by_day,
    get_active_model_version, count_products_for_client, product_exists, count_trainings_today,
    MANUAL, AUTO, FREE_TIER_PRODUCT_LIMIT, FREE_TIER_MANUAL_TRAINING_DAILY_LIMIT,
    FREE_TIER_AUTO_RETRAIN_DAILY_LIMIT, utcnow,
)

# Make sure the products/users/interactions/clients tables exist - harmless no-op if they do.
init_db()
ensure_client_id(DEMO_CLIENT_ID, "LIKYLY Demo")

# The catalog namespace ("movies", "acme-shop", ...) is caller-defined, not a fixed
# enum - a customer's own catalog isn't known in advance. Kept as a string with a
# conservative format so it stays safe to use as a partition key.
ProductType = Annotated[str, Query(
    min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$',
    description="Catalog namespace, e.g. 'movies' or a customer's own catalog name",
)]

#Stopwords dir
stopwords_relative_path = '../../data/stopwords'
stopwords_dir = os.path.abspath(os.path.join(current_dir, stopwords_relative_path))


tags_metadata = [
    {
        "name": "getProducts",
        "description": "List of products or one product",
    },
    {
        "name": "getUsers",
        "description": "List of users",
    },
    {
        "name": "getUsersPurchases",
        "description": "List of users purchases",
    },
    {
        "name": "getUsersRatings",
        "description": "List of users ratings",
    },
    {
        "name": "getUsersPageViews",
        "description": "List of users page views",
    },
    {
        "name": "generateModel",
        "description": "Trigger Machine Learning model training as a background job, and poll its status.",
    },
    {
        "name": "getRecContent",
        "description": "Get a list of recommendated works based on similar features of products - Content-based Filtering",
    },
    {
        "name": "getRecContentVectorCreateIndex",
        "description": (
            "[Legacy/frozen] Create a Pinecone index from product embeddings - "
            "https://app.pinecone.io/. Superseded by pgvector-backed semantic similarity, "
            "now blended directly into /getRec/content - kept working for existing "
            "integrations, not recommended for new ones."
        ),
    },
    {
        "name": "getRecContentVectorDb",
        "description": (
            "[Legacy/frozen] Content-based recommendations via a Pinecone vector index. "
            "Superseded by pgvector-backed semantic similarity, now blended directly into "
            "/getRec/content (same embedding model, no separate vector database to run) - "
            "kept working for existing integrations, not recommended for new ones."
        ),
    },
    {
        "name": "getRecCollaborative",
        "description": "Get a list of recommendated works for a user based on others users ratings/purchase - User-based Collaborative Filtering",
    },
    {
        "name": "getRecHybrid",
        "description": "Get a list of recommended works blending content-based similarity and collaborative filtering, weighted by 'alpha'",
    },
    {
        "name": "getRecSession",
        "description": "Get a list of recommended works based on a list of recently viewed work_ids - no user_id or login required",
    },
    {
        "name": "productsIngestion",
        "description": "Push/update the lightweight content profile (title, description, category) of your own products - only needed for content-based, hybrid or session recommendations. Price, stock and images stay in your own system.",
    },
    {
        "name": "eventsIngestion",
        "description": "Send purchase/view events referencing your own product and user ids. Required for collaborative filtering; no product catalog needs to be shared for this alone.",
    },
]

# Every request must authenticate as a client (tenant): the API key resolves to a
# client_id, and every query/write below is scoped to that client_id. This is what
# keeps two customers' catalogs and events from ever mixing in the shared database,
# even if they happen to pick the same product_type name.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _resolve_client_id(
    background_tasks: BackgroundTasks, api_key: Optional[str], min_scope: str,
) -> int:
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
    return client_id


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
    with the restricted public key: reading recommendations/products, and recording
    purchase/view events - see the two-tier key architecture in db.py's ClientModel."""
    return await _resolve_client_id(background_tasks, api_key, "public")


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
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=401, detail=f"Invalid session token: {error}")


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


def normalize_scores(series: pd.Series) -> pd.Series:
    """Min-max normalize a score series to [0, 1] so two differently-scaled signals
    (content cosine similarity, ALS score) can be blended with a meaningful weight."""
    if series.empty:
        return series
    span = series.max() - series.min()
    if span == 0:
        return series * 0.0 + 1.0
    return (series - series.min()) / span


def diversify_by_genre(candidates: pd.DataFrame, allowed_genres: set, count: int, genre_first: bool = False) -> pd.DataFrame:
    """Guarantees at least half the results share a genre the visitor has shown interest
    in. Fixes a real failure mode: a single candidate can score anomalously high on both
    TF-IDF and semantic similarity purely from a coincidental shared phrase (e.g. "The Bad
    Guys 2" naming its rival gang "The Bad Girls" in-story, which text similarity reads as
    a strong match to an unrelated film literally titled "Bad Girls") and crowd out every
    genre-appropriate alternative.

    genre_first=False (default, used by /getRec/content - the "Pourquoi ?"/Cold start
    pages): guarantees inclusion but re-ranks the union by raw score, so a genuinely
    dominant cross-genre match (Jurassic Park for Jurassic World Rebirth, Avengers sequels
    for The Avengers) still wins the top spot on merit.

    genre_first=True (used by /getRec/session - "Vous aimerez aussi" on the homepage/
    product pages): genre-matched candidates are placed ahead of cross-genre ones
    regardless of raw score. Verified empirically that no score-based reweighting can
    separate a spurious cross-genre match (Bad Girls) from a genuine one (Jurassic Park) -
    both dominate their pool by a comparable margin on every available signal - so fixing
    one via score alone would have silently broken the other. This trades away Jurassic
    Park's top spot in this specific endpoint to guarantee Bad Guys 2 surfaces real family
    films first; the "Pourquoi ?" page keeps the other behavior."""
    if not allowed_genres:
        return candidates.sort_values('score', ascending=False).head(count)

    ranked = candidates.sort_values('score', ascending=False)
    quota = math.ceil(count / 2)
    same_genre = ranked[ranked['genre_1'].isin(allowed_genres)].head(quota)
    remaining = count - len(same_genre)
    others = ranked[~ranked['work_id'].isin(same_genre['work_id'])].head(remaining)
    if genre_first:
        return pd.concat([same_genre, others])
    return pd.concat([same_genre, others]).sort_values('score', ascending=False)


def compute_session_recs(product_type: str, client_id: int, viewed_ids: list, count: int) -> list:
    """Recency-weighted content recs from a list of recently viewed work_ids - shared by
    the stateless /getRec/session (anonymous, client-supplied list) and
    /getRec/sessionForUser (logged-in, list sourced from persisted history) endpoints,
    so both personalization paths rank recs exactly the same way."""
    data_works = get_data(product_type, product_id=None, count=None, client_id=client_id)
    valid_viewed_ids = [wid for wid in viewed_ids if (data_works['work_id'] == wid).any()]
    if not valid_viewed_ids:
        return []

    data_similarities = get_data_similarities(data_works)
    cosine_sim, indices = get_cosine_similarities_cached(
        data_works, data_similarities['bag_of_words'], stopwords_terms, "Tfidf", client_id, product_type
    )

    work_id_to_idx = dict(zip(data_works['work_id'], range(len(data_works))))

    # Recency weighting: the most recently viewed item (last in the list) counts most
    n = len(valid_viewed_ids)
    weights = [(i + 1) / n for i in range(n)]

    combined_scores = np.zeros(cosine_sim.shape[0])
    for work_id, weight in zip(valid_viewed_ids, weights):
        combined_scores += weight * cosine_sim[work_id_to_idx[work_id]]

    viewed_idx_set = {work_id_to_idx[wid] for wid in valid_viewed_ids}
    order = np.argsort(combined_scores)[::-1]

    # Genres the visitor has shown interest in across everything viewed so far - used
    # below to keep one anomalously-scoring cross-genre match from crowding out every
    # genre-appropriate alternative (see diversify_by_genre).
    viewed_genres = set(data_works.loc[data_works['work_id'].isin(valid_viewed_ids), 'genre_1'].dropna())

    pool_size = min(len(data_works), count * 5 + 1)
    pool_rows = []
    for idx in order:
        if idx in viewed_idx_set:
            continue

        # Which viewed item most drove this particular recommendation
        best_source_wid, best_sim = None, -1.0
        for source_wid in valid_viewed_ids:
            sim = cosine_sim[work_id_to_idx[source_wid], idx]
            if sim > best_sim:
                best_sim, best_source_wid = sim, source_wid
        source_title = data_works.loc[data_works['work_id'] == best_source_wid, 'title'].iloc[0]

        row = data_works.iloc[idx].to_dict()
        row['score'] = float(combined_scores[idx])
        row['explanation'] = {
            "reason": f"Similaire à « {source_title} », consulté récemment",
            "content_similarity": float(best_sim),
            "source_work_ids": valid_viewed_ids,
        }
        pool_rows.append(row)
        if len(pool_rows) >= pool_size:
            break

    if not pool_rows:
        return []

    final_df = diversify_by_genre(pd.DataFrame(pool_rows), viewed_genres, count, genre_first=True)
    return json.loads(final_df.to_json(orient='records', date_format='iso'))


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
        GENERATE_MODEL_JOBS[job_id].update(status="failed", detail=str(error))


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

    if client_id != DEMO_CLIENT_ID and count_trainings_today(client_id, product_type, AUTO) >= FREE_TIER_AUTO_RETRAIN_DAILY_LIMIT:
        _auto_retrain_skips[key] = {
            "skipped_at": utcnow(),
            "reason": (
                f"Free plan limit reached: {FREE_TIER_AUTO_RETRAIN_DAILY_LIMIT} automatic "
                f"retrain(s) per day. {new_interactions} new interactions are waiting - "
                "the model will catch up on tomorrow's automatic retrain, or upgrade your plan."
            ),
        }
        return

    _auto_retrain_skips.pop(key, None)
    _auto_retrain_in_progress.add(key)
    job_id = str(uuid.uuid4())
    GENERATE_MODEL_JOBS[job_id] = {
        "job_id": job_id, "status": "queued", "data_product_type": product_type,
        "detail": f"Auto-triggered: {new_interactions} new interactions since last training",
        "version_id": None, "precision_at_k": None, "promoted": None,
    }
    background_tasks.add_task(run_auto_retrain_job, job_id, client_id, product_type, key)


app_description = (
    "API to serve product recommendations based on Machine Learning - AI models, for any product catalog.\n\n"
    "- Multi-tenant: every request authenticates via X-API-Key to a specific client, whose catalog/events/model are fully isolated\n"
    "- Two-tier keys: a secret key (full access, server-side only) and a restricted public key "
    "(recommendations + interaction tracking only) safe to embed in client-side JS\n"
    "- Catalog ingestion: push your own products (content profile) and purchase/view events\n"
    "- Content-based recommendations: TF-IDF cosine similarity blended with pgvector semantic "
    "embeddings (sentence-transformers), computed at ingestion time and stored in Postgres - "
    "no separate vector database to run\n"
    "- Collaborative filtering powered by implicit ALS, with model versioning/promotion gating "
    "and MLflow tracking\n"
    "- Legacy Pinecone-based vector endpoints are frozen (kept working, superseded by the "
    "pgvector integration above)"
)

app = FastAPI(title="ML API - Predict Rec Products",
              description=app_description,
              version="0.0.1",
              openapi_tags=tags_metadata,
              root_path="/recsys-api"
              )


class Item(BaseModel):
    count: int = Field(default='3')
    user_name: int = Field(default='Arnaud Breton')

    #df: Json[Any] = Field(default='{"count": 3}')

@app.get("/")
async def root():
    return {"message": "API- Recommandation System based either on Machine Learning Model Spotlight TfidfVectorizer or Pinecone with encoding model all-MiniLM-L12-v2"}

@app.post("/clients/me", tags=["selfServiceClient"], response_model=ClientSelf)
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
        return {"client_id": existing["id"], **{k: v for k, v in existing.items() if k != "id"}}

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
    }

@app.post("/clients/me/regenerate-secret-key", tags=["selfServiceClient"], response_model=ClientSelf)
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
    }

@app.post("/clients/me/regenerate-public-key", tags=["selfServiceClient"], response_model=ClientSelf)
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
    }

@app.get("/admin/clients", tags=["admin"], response_model=List[ClientAdminView])
async def admin_list_clients(admin_user_id: str = Depends(get_current_admin_user_id)):
    """Operator-only: every client across the whole system, not scoped to the caller's
    own account - gated by app_metadata.is_admin, entirely separate from the
    self-service /clients/me* endpoints above."""
    return list_all_clients_with_usage()

@app.get("/admin/clients/{client_id}", tags=["admin"], response_model=ClientAdminView)
async def admin_get_client(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Single-client detail (for the admin panel's side drawer) - same shape as one row
    of GET /admin/clients, without re-fetching the whole list."""
    row = get_client_admin_row(client_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return row

@app.patch("/admin/clients/{client_id}", tags=["admin"], response_model=ClientAdminView)
async def admin_update_client(client_id: int, payload: ClientRename, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Currently only renames the client - contact_email is intentionally not editable
    here, since it's re-synced from the linked Supabase account's JWT on every login
    (see get_or_create_my_client) and a manual edit would just get silently overwritten."""
    if not rename_client(client_id, payload.name):
        raise HTTPException(status_code=404, detail="Client not found")
    return get_client_admin_row(client_id)

@app.get("/admin/clients/{client_id}/usage", tags=["admin"], response_model=List[DailyUsage])
async def admin_client_usage(client_id: int, days: int = 30, admin_user_id: str = Depends(get_current_admin_user_id)):
    return get_client_usage_by_day(client_id, days)

@app.post("/admin/clients/{client_id}/disable", tags=["admin"], response_model=Message)
async def admin_disable_client(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Suspends a client: both its keys stop authenticating immediately (see
    get_client_and_scope_by_api_key's is_active check), without touching their data -
    reversible via /admin/clients/{client_id}/enable."""
    if not set_client_active(client_id, False):
        raise HTTPException(status_code=404, detail="Client not found")
    return {"message": "Client disabled"}

@app.post("/admin/clients/{client_id}/enable", tags=["admin"], response_model=Message)
async def admin_enable_client(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    if not set_client_active(client_id, True):
        raise HTTPException(status_code=404, detail="Client not found")
    return {"message": "Client enabled"}

@app.post("/admin/clients/{client_id}/revoke-secret-key", tags=["admin"], response_model=Message)
async def admin_revoke_secret_key(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    """Kills the client's secret key immediately, with no replacement - use this for
    incident response (e.g. a leaked key), when the point is to cut access off right now
    and let the client self-serve a new one whenever they're ready. If the goal is
    instead to hand the client a working key over a support channel, use
    regenerate-secret-key below."""
    revoke_secret_key(client_id)
    return {"message": "Secret key revoked - the client must generate a new one from their account page"}

@app.post("/admin/clients/{client_id}/revoke-public-key", tags=["admin"], response_model=Message)
async def admin_revoke_public_key(client_id: int, admin_user_id: str = Depends(get_current_admin_user_id)):
    revoke_public_key(client_id)
    return {"message": "Public key revoked - the client must generate a new one from their account page"}

@app.post("/admin/clients/{client_id}/regenerate-secret-key", tags=["admin"], response_model=ClientSelf)
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

@app.post("/admin/clients/{client_id}/regenerate-public-key", tags=["admin"], response_model=ClientSelf)
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

@app.delete("/admin/clients/{client_id}", tags=["admin"], response_model=Message)
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

@app.get("/products", tags=["getProducts"], response_model=List[Product])
async def getProducts(data_product_type: ProductType, product_id: Optional[int] = None, count: Optional[int] = None, client_id: int = Depends(get_current_client_id_public_ok)):
    # List of products (works, movies, shows)
    return to_records_or_404(get_data(data_product_type, product_id, count, client_id=client_id))

@app.get("/users", tags=["getUsers"], response_model=List[User])
async def getUsers(data_product_type: ProductType, user_id: Optional[int] = None, count: Optional[int] = None, client_id: int = Depends(get_current_client_id)):
    # List of users
    return to_records_or_404(get_data_users(data_product_type, user_id, count, client_id=client_id))

@app.get("/usersPurchases", tags=["getUsersPurchases"], response_model=List[Purchase])
async def getUsersPurchases(data_product_type: ProductType, user_id: Optional[int] = None, count: Optional[int] = None, client_id: int = Depends(get_current_client_id)):
    # List of users purchases
    return to_records_or_404(get_data_users_purchases(data_product_type, user_id, count, client_id=client_id))

@app.get("/usersRatings", tags=["getUsersRatings"], response_model=List[Rating])
async def usersRatings(data_product_type: ProductType, user_id: Optional[int] = None, count: Optional[int] = None, client_id: int = Depends(get_current_client_id)):
    # List of users ratings
    return to_records_or_404(get_data_users_ratings(data_product_type, user_id, count, client_id=client_id))

@app.get("/usersPageViews", tags=["getUsersPageViews"], response_model=List[PageView])
async def usersPageViews(data_product_type: ProductType, user_id: Optional[int] = None, count: Optional[int] = None, client_id: int = Depends(get_current_client_id)):
    # List of users page views
    return to_records_or_404(get_data_users_page_views(data_product_type, user_id, count, client_id=client_id))

@app.put("/products/{product_id}/profile", tags=["productsIngestion"], response_model=Message)
async def upsert_product_profile_endpoint(data_product_type: ProductType, product_id: int, payload: ProductProfileUpsert, client_id: int = Depends(get_current_client_id)):
    # Only a brand-new product counts against the cap - updating an existing one's
    # profile must stay possible even once the free-tier catalog is full.
    is_new_product = not product_exists(client_id, data_product_type, product_id)
    if client_id != DEMO_CLIENT_ID and is_new_product and count_products_for_client(client_id) >= FREE_TIER_PRODUCT_LIMIT:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Free plan limit reached: {FREE_TIER_PRODUCT_LIMIT} products max. "
                "Upgrade your plan to add more products."
            ),
        )

    upsert_product_profile(
        product_type=data_product_type,
        work_id=product_id,
        title=payload.title,
        description=payload.description,
        genre_1=payload.genre_1,
        author=payload.author,
        year=payload.year,
        url=payload.url,
        price=payload.price,
        client_id=client_id,
    )

    try:
        compute_and_store_product_embedding(
            client_id=client_id, product_type=data_product_type, work_id=product_id,
            title=payload.title, description=payload.description, genre_1=payload.genre_1,
        )
    except Exception as error:
        # The profile itself is already saved and content-based (TF-IDF) recs work fine
        # without an embedding - a failure here shouldn't fail the whole upsert.
        print(f"Embedding computation skipped (non-fatal): {error}")

    return {"message": f"Product profile {product_id} upserted for data_product_type={data_product_type}"}

@app.delete("/products/{product_id}/profile", tags=["productsIngestion"], response_model=Message)
async def delete_product_profile_endpoint(data_product_type: ProductType, product_id: int, client_id: int = Depends(get_current_client_id)):
    deleted = delete_product_profile(product_type=data_product_type, work_id=product_id, client_id=client_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No product profile {product_id} for data_product_type={data_product_type}")
    return {"message": f"Product profile {product_id} deleted for data_product_type={data_product_type}"}

@app.post("/events/purchase", tags=["eventsIngestion"], response_model=Message)
async def record_purchase_event(
    data_product_type: ProductType, payload: PurchaseEvent, background_tasks: BackgroundTasks,
    client_id: int = Depends(get_current_client_id_public_ok),
):
    insert_interaction(
        product_type=data_product_type,
        work_id=payload.work_id,
        user_id=payload.user_id,
        event_type=PURCHASE,
        quantity=payload.quantity,
        occurred_at=payload.occurred_at,
        client_id=client_id,
    )
    maybe_trigger_auto_retrain(client_id, data_product_type, background_tasks)
    return {"message": "Purchase event recorded"}

@app.post("/events/view", tags=["eventsIngestion"], response_model=Message)
async def record_view_event(
    data_product_type: ProductType, payload: ViewEvent, background_tasks: BackgroundTasks,
    client_id: int = Depends(get_current_client_id_public_ok),
):
    insert_interaction(
        product_type=data_product_type,
        work_id=payload.work_id,
        user_id=payload.user_id,
        event_type=VIEW,
        quantity=1,
        occurred_at=payload.occurred_at,
        client_id=client_id,
    )
    maybe_trigger_auto_retrain(client_id, data_product_type, background_tasks)
    return {"message": "View event recorded"}

@app.get("/generateModel", tags=["generateModel"], response_model=GenerateModelJobStatus)
async def generate_model(data_product_type: ProductType, background_tasks: BackgroundTasks, client_id: int = Depends(get_current_client_id)):
    product_type = data_product_type

    if client_id != DEMO_CLIENT_ID and count_trainings_today(client_id, product_type, MANUAL) >= FREE_TIER_MANUAL_TRAINING_DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Free plan limit reached: {FREE_TIER_MANUAL_TRAINING_DAILY_LIMIT} manual "
                "training run(s) per day. Try again tomorrow, or upgrade your plan for more."
            ),
        )

    job_id = str(uuid.uuid4())

    GENERATE_MODEL_JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "data_product_type": product_type,
        "detail": None,
        "version_id": None,
        "precision_at_k": None,
        "promoted": None,
    }
    background_tasks.add_task(run_generate_model_job, job_id, client_id, product_type, MANUAL)

    return GENERATE_MODEL_JOBS[job_id]

@app.get("/generateModel/status/{job_id}", tags=["generateModel"], response_model=GenerateModelJobStatus)
async def generate_model_status(job_id: str, client_id: int = Depends(get_current_client_id)):
    job = GENERATE_MODEL_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Unknown job_id {job_id}")
    return job

@app.get("/models/versions", tags=["generateModel"], response_model=List[ModelVersion])
async def get_model_versions(data_product_type: ProductType, client_id: int = Depends(get_current_client_id)):
    """History of trained ALS models for this client/product type - hyperparameters,
    precision@k, and which one is currently active (served)."""
    return list_model_versions(client_id, data_product_type)

@app.get("/models/status", tags=["generateModel"], response_model=ModelStatus)
async def get_model_status(data_product_type: ProductType, client_id: int = Depends(get_current_client_id)):
    """Everything a client dashboard needs to show "your model" in one call: the active
    version - including whether it came from a manual call or an automatic retrain, and
    its date - plus today's training quota usage, so a free-tier client can see clearly
    why a training run was refused or an auto-retrain skipped, instead of just noticing
    nothing changed."""
    is_demo = client_id == DEMO_CLIENT_ID
    skip = _auto_retrain_skips.get((client_id, data_product_type))
    skip_today = skip is not None and skip["skipped_at"].date() == utcnow().date()

    return {
        "data_product_type": data_product_type,
        "active_version": get_active_model_version(client_id, data_product_type),
        "manual_trainings_today": count_trainings_today(client_id, data_product_type, MANUAL),
        "manual_training_daily_limit": None if is_demo else FREE_TIER_MANUAL_TRAINING_DAILY_LIMIT,
        "auto_retrains_today": count_trainings_today(client_id, data_product_type, AUTO),
        "auto_retrain_daily_limit": None if is_demo else FREE_TIER_AUTO_RETRAIN_DAILY_LIMIT,
        "auto_retrain_skipped_today": skip_today,
        "auto_retrain_skip_reason": skip["reason"] if skip_today else None,
    }

@app.get("/getRec/popular/{count}", tags=["getRecContent"], response_model=List[RecommendedProduct])
async def get_rec_popular(data_product_type: ProductType, count: int, client_id: int = Depends(get_current_client_id_public_ok)):
    """Pure popularity ranking, no anchor product or user history needed - the true
    cold-start fallback for a visitor with zero browsing history at all (not even one
    viewed product to compute content similarity from)."""
    data_works = get_data(data_product_type, product_id=None, count=None, client_id=client_id)
    popularity = compute_popularity_scores(data_product_type, client_id=client_id)
    if popularity.empty:
        return []

    merged = data_works.merge(popularity, on='work_id', how='inner')
    merged = merged.sort_values('popularity_score', ascending=False).head(count)

    records = json.loads(merged.to_json(orient='records', date_format='iso'))
    for record in records:
        purchase_count = record.get('purchase_count')
        purchase_count = int(purchase_count) if purchase_count is not None else None
        record['score'] = record.get('popularity_score')
        record['explanation'] = {
            "reason": f"Populaire ({purchase_count} achats)" if purchase_count else "Recommandation populaire",
            "popularity_score": record.get('popularity_score'),
            "purchase_count": purchase_count,
        }
    return records

@app.get("/getRec/content/{product_id}/{count}", tags=["getRecContent"], response_model=List[RecommendedProduct])
async def get_rec_content(data_product_type: ProductType, product_id: int, count: int, client_id: int = Depends(get_current_client_id_public_ok)):
    #try:
        product_type = data_product_type
        # List of works
        data_works = get_data(product_type, product_id=None, count=None, client_id=client_id)
        # Get Work Title from the ID
        title = data_works.loc[data_works['work_id'] == product_id, 'title'].iloc[0]
        # create bags of words
        data_similarities = get_data_similarities(data_works)

        # create cosine similarities matrix (must fit on the bag_of_words text column,
        # not the whole DataFrame, or CountVectorizer silently vectorizes column names)
        cosine_sim, indices = get_cosine_similarities_cached(
            data_works, data_similarities['bag_of_words'], stopwords_terms, "Tfidf", client_id, product_type
        )

        # Wide TF-IDF candidate pool, blended below with semantic embedding neighbors -
        # TF-IDF alone only matches shared vocabulary; embeddings also catch paraphrased/
        # thematically similar synopses that share no literal words.
        pool_size = min(len(data_works), count * 5 + 1)
        tfidf_candidates = model_content_recommender(title, cosine_sim, data_works, indices, limit=pool_size, with_score=False)

        semantic_neighbors = find_similar_by_embedding(client_id, product_type, product_id, count=pool_size - 1)
        semantic_map = {n["work_id"]: n["semantic_similarity"] for n in semantic_neighbors}

        # Union of both candidate sets, so a strong semantic-only match isn't dropped just
        # because it fell outside the narrower TF-IDF pool, and vice versa.
        candidate_ids = set(tfidf_candidates["work_id"].tolist()) | set(semantic_map.keys())
        candidates = data_works[data_works["work_id"].isin(candidate_ids)].copy()

        tfidf_map = dict(zip(tfidf_candidates["work_id"], tfidf_candidates["content_similarity"]))
        candidates["content_similarity"] = candidates["work_id"].map(tfidf_map).fillna(0.0)
        candidates["semantic_similarity"] = candidates["work_id"].map(semantic_map).fillna(0.0)

        tfidf_norm = normalize_scores(candidates["content_similarity"])
        semantic_norm = normalize_scores(candidates["semantic_similarity"])
        candidates["score"] = (1 - SEMANTIC_BLEND_WEIGHT) * tfidf_norm + SEMANTIC_BLEND_WEIGHT * semantic_norm
        source_genre = data_works.loc[data_works['work_id'] == product_id, 'genre_1'].iloc[0]
        candidates = diversify_by_genre(candidates, {source_genre} if source_genre else set(), count)

        popularity = compute_popularity_scores(product_type, client_id=client_id)
        merged = candidates.merge(popularity, on='work_id', how='left')

        records = json.loads(merged.to_json(orient='records', date_format='iso'))
        for record in records:
            purchase_count = record.get('purchase_count')
            purchase_count = int(purchase_count) if purchase_count is not None else None
            record['explanation'] = {
                "reason": (
                    f"Similaire à « {title} » par le contenu (texte + similarité sémantique)"
                    + (f", populaire ({purchase_count} achats)" if purchase_count else "")
                ),
                "content_similarity": record.get('content_similarity'),
                "semantic_similarity": record.get('semantic_similarity'),
                "popularity_score": record.get('popularity_score'),
                "purchase_count": purchase_count,
            }
        return records

    #except Exception as error:
    #    return {'error': error}

@app.get("/getRec/contentVec/createIndex", tags=["getRecContentVectorCreateIndex"], response_model=Message)
async def get_rec_content_vectordb_init(data_product_type: ProductType, client_id: int = Depends(get_current_client_id)):
    #try:
        # List of works
        data_works = get_data(data_product_type, product_id=None, count=None, client_id=client_id)
        # create bag of words
        data_similarities = get_data_similarities(data_works)
        # get only bag of words and convert it to dictionnary
        data_similarities["id"] = data_similarities["work_id"].astype(str)
        # Transform list of works into dictionnary
        data_similarities_dict = data_similarities.to_dict(orient='records')
        data_similarities_prepared_for_vectors = data_similarities[["id","bag_of_words"]].to_dict(orient='records')

        index, model, total_vectors = model_vector_indexing(
            data_similarities_dict,
            data_similarities_prepared_for_vectors,
            data_product_type,
            client_id=client_id,
        )

        endpoint_response = {
            "message": f"OK, Vector Index was created and total of {total_vectors} vectors were added to the index"
        }

        return endpoint_response

    #except Exception as error:
    #    return {'error': error}

@app.get("/getRec/contentVec/{product_id}/{count}", tags=["getRecContentVectorDb"], response_model=List[VectorRecommendation])
async def get_rec_content_vectordb(data_product_type: ProductType, product_id: int, count: int, client_id: int = Depends(get_current_client_id_public_ok)):
    #try:
        # List of works
        data_works = get_data(data_product_type, product_id=None, count=None, client_id=client_id)
        # Get Work Title from the ID
        title = data_works.loc[data_works['work_id'] == product_id, 'title'].iloc[0]
        # create bag of words
        data_similarities = get_data_similarities(data_works)
        # get only bag of words and convert it to dictionnary
        data_similarities["id"] = data_similarities["work_id"].astype(str)
        # Transform list of works into dictionnary
        data_similarities_dict = data_similarities.to_dict(orient='records')
        data_similarities_prepared_for_vectors = data_similarities[["id","bag_of_words"]].to_dict(orient='records')

        print("title:",title)
        #print(data_works[:3])
        #print(data_similarities_prepared_for_vectors[:3])

        predictOutput = model_content_recommender_vectors(
            data_similarities_dict,
            data_similarities_prepared_for_vectors,
            title,
            count,
            data_product_type,
            client_id=client_id,
        )

        return predictOutput

    #except Exception as error:
    #    return {'error': error}


@app.get("/getRec/collaborative/{user_id}/{count}", tags=["getRecCollaborative"], response_model=List[RecommendedProduct])
async def get_rec_collaborative(data_product_type: ProductType, user_id: int, count: int, client_id: int = Depends(get_current_client_id_public_ok)):
        # List of works
        data_works = get_data(data_product_type, product_id=None, count=None, client_id=client_id)

        # List of works purchased by users - exclusion of works products buy previously by the user - to not rec those to him
        data_purchases = get_data_users_purchases(data_product_type, user_id=None, count=None, client_id=client_id)

        try:
            return predict_items_from_user_api(data_product_type, data_works, data_purchases, user_id, count, client_id=client_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error))
        except Exception as error:
            raise HTTPException(status_code=500, detail=f"Collaborative model prediction failed: {error}")


@app.get("/getRec/hybrid/{user_id}/{product_id}/{count}", tags=["getRecHybrid"], response_model=List[RecommendedProduct])
async def get_rec_hybrid(data_product_type: ProductType, user_id: int, product_id: int, count: int, alpha: float = 0.5, client_id: int = Depends(get_current_client_id_public_ok)):
    product_type = data_product_type

    data_works = get_data(product_type, product_id=None, count=None, client_id=client_id)
    title = data_works.loc[data_works['work_id'] == product_id, 'title'].iloc[0]

    data_similarities = get_data_similarities(data_works)
    cosine_sim, indices = get_cosine_similarities_cached(
        data_works, data_similarities['bag_of_words'], stopwords_terms, "Tfidf", client_id, product_type
    )

    # Wide content-based candidate pool, then re-ranked by blending in the collaborative signal
    pool_size = min(len(data_works), count * 5 + 1)
    candidates = model_content_recommender(title, cosine_sim, data_works, indices, limit=pool_size, with_score=False)

    collab_scores = {}
    try:
        rec_model = load_model(product_type, client_id=client_id)
        interactions = build_user_item_matrix(product_type, client_id=client_id)
        user_items_row = interactions[user_id] if user_id < interactions.shape[0] else None
        candidate_ids = candidates['work_id'].to_numpy()
        item_ids, scores = rec_model.recommend(
            user_id, user_items_row, N=len(candidate_ids),
            filter_already_liked_items=False, items=candidate_ids,
        )
        collab_scores = dict(zip(item_ids, scores))
    except FileNotFoundError:
        pass  # no trained model yet - degrade gracefully to pure content-based ranking

    content_norm = normalize_scores(candidates.set_index('work_id')['content_similarity'])
    collab_norm = normalize_scores(pd.Series(collab_scores, dtype=float)) if collab_scores else pd.Series(dtype=float)

    rows = []
    for _, row in candidates.iterrows():
        work_id = row['work_id']
        c_score = float(content_norm.get(work_id, 0.0))
        cf_score = float(collab_norm.get(work_id, 0.0))
        record = row.to_dict()
        record['score'] = alpha * cf_score + (1 - alpha) * c_score
        record['explanation'] = {
            "reason": f"Hybride : {alpha:.0%} collaboratif + {1 - alpha:.0%} contenu (similaire à « {title} »)",
            "content_similarity": c_score,
            "collaborative_score": cf_score if collab_scores else None,
        }
        rows.append(record)

    rows.sort(key=lambda r: r['score'], reverse=True)

    return json.loads(pd.DataFrame(rows[:count]).to_json(orient='records', date_format='iso'))


@app.get("/getRec/session", tags=["getRecSession"], response_model=List[RecommendedProduct])
async def get_rec_session(data_product_type: ProductType, viewed_work_ids: str, count: int = 3, client_id: int = Depends(get_current_client_id_public_ok)):
    """Recommendations from a list of recently viewed work_ids - no account/login needed.
    The client (SDK) is expected to keep this list in browser storage and send it on
    each call; the API itself stays stateless."""
    try:
        viewed_ids = [int(x) for x in viewed_work_ids.split(',') if x.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail="viewed_work_ids must be a comma-separated list of integers")

    if not viewed_ids:
        raise HTTPException(status_code=422, detail="viewed_work_ids must contain at least one work_id")

    recs = compute_session_recs(data_product_type, client_id, viewed_ids, count)
    if not recs:
        raise HTTPException(status_code=404, detail="None of the given viewed_work_ids exist in the catalog")
    return recs


@app.get("/getRec/sessionForUser/{user_id}/{count}", tags=["getRecSession"], response_model=List[RecommendedProduct])
async def get_rec_session_for_user(data_product_type: ProductType, user_id: int, count: int, client_id: int = Depends(get_current_client_id_public_ok)):
    """Same recency-weighted content recs as /getRec/session, but for a logged-in user:
    the recently-viewed list is sourced from persisted view history (Postgres) instead
    of a client-supplied list - so "for you" recs survive across devices/sessions."""
    viewed_ids = get_recent_viewed_work_ids(client_id, data_product_type, user_id, limit=10)
    if not viewed_ids:
        return []
    return compute_session_recs(data_product_type, client_id, viewed_ids, count)


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


if __name__ == "__main__":
    #uvicorn.run('app:app', host='127.0.0.1', port=80)
    uvicorn.run('app:app', host='0.0.0.0', port=6061)
