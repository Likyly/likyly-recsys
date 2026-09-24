"""Postgres-backed, multi-tenant storage for the product catalog and interaction events.

Every table is scoped by `client_id` in addition to `product_type`: two different
customers can both use a catalog namespace called "shop" without colliding, because
the real partition key is (client_id, product_type), not product_type alone. A
client's API key (hashed) is the only way to resolve a client_id - see
create_client()/get_client_and_scope_by_api_key().

Content profile (title/description/genre_1) is optional: only needed for content-based,
hybrid or session recommendations. Collaborative filtering only needs `interactions`.
"""
import hashlib
import os
import secrets
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd
from dotenv import load_dotenv
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    create_engine,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.orm import declarative_base, sessionmaker

# Dimension of sentence-transformers/all-MiniLM-L12-v2, the model used for semantic
# content embeddings (see modelData.compute_embedding) - fixed by the model architecture.
EMBEDDING_DIM = 384

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(CURRENT_DIR, "..", "api", ".env"))

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set (expected in application/api/.env)")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

PURCHASE = "purchase"
VIEW = "view"
# The other officially supported event types (see DEFAULT_EVENT_TYPES below) - any other
# string is still accepted and auto-registered, these are just the ones every tenant
# starts with.
IMPRESSION = "impression"
CLICK = "click"
ADD_TO_CART = "add_to_cart"
REMOVE_FROM_CART = "remove_from_cart"

MANUAL = "manual"
AUTO = "auto"

# Fixed id for our own demo catalog (movies/books/shoes) and for internal callers
# (Solara pages) that talk to these functions directly, bypassing the HTTP API and its
# per-client API key auth entirely - they always operate as this one client.
DEMO_CLIENT_ID = 1

# Plan names are the only vocabulary PLAN_LIMITS understands. "free" is the only plan a
# self-service signup can reach today; "unlimited" is what the demo client gets, and what
# an admin can manually grant a client via PATCH /admin/clients/{id} (e.g. a pilot
# customer) - paid tiers are a placeholder for future work, not wired to billing yet.
PLAN_FREE = "free"
PLAN_UNLIMITED = "unlimited"
VALID_PLANS = {PLAN_FREE, PLAN_UNLIMITED}

PLAN_LIMITS: dict[str, dict[str, Optional[int]]] = {
    PLAN_FREE: {
        "product_limit": 50,
        "manual_training_daily_limit": 1,
        "auto_retrain_daily_limit": 1,
    },
    PLAN_UNLIMITED: {
        "product_limit": None,
        "manual_training_daily_limit": None,
        "auto_retrain_daily_limit": None,
    },
}


def get_plan_limits(plan: str) -> dict[str, Optional[int]]:
    """Falls back to the free tier's limits for any unrecognized plan string (e.g. a
    typo written directly in Postgres) instead of raising - fails closed to the most
    restrictive tier rather than crashing every quota check across the API."""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS[PLAN_FREE])


# A tenant's own event types ("purchase", "reservation", "watch", ...) aren't a fixed
# enum - different verticals have different signals of interest (a library's
# reservations, a video platform's watch time, a content site's page views). Instead of
# exposing a raw ALS confidence weight (meaningless to a non-technical tenant), each
# event type is tagged with one of these three tiers - matches the exact weights
# purchase/view already used before this generalization, so existing behavior for those
# two types is unchanged; "moyen" is the new middle tier for anything in between (e.g.
# "add to cart", "favorited").
EVENT_TIER_WEIGHTS = {"aucun": 0.0, "faible": 0.2, "moyen": 1.0, "fort": 3.0}
DEFAULT_EVENT_TIER = "moyen"

# Event types every tenant starts with. "aucun" (weight 0) is for events that are worth
# recording - impressions are the denominator of any CTR, remove_from_cart is a useful
# funnel signal - but that must NOT count as interest: an impression is exposure caused by
# our own recommendation, and feeding it back into training/popularity would create a
# feedback loop (recommend X -> X is "seen" -> X looks more popular). Weight-0 rows are
# excluded from the ALS matrix, from popularity, and from the auto-retrain trigger.
DEFAULT_EVENT_TYPES = [
    (PURCHASE, "Achat", "fort"),
    (VIEW, "Vue", "faible"),
    (IMPRESSION, "Impression", "aucun"),
    (CLICK, "Clic", "faible"),
    (ADD_TO_CART, "Ajout au panier", "moyen"),
    (REMOVE_FROM_CART, "Retrait du panier", "aucun"),
]


def get_event_tier_weight(tier: str) -> float:
    """Falls back to the middle tier for an unrecognized tier string - same fail-safe-
    default reasoning as get_plan_limits, but there's no obviously "safe" direction here
    (unlike quotas), so the fallback is simply the middle of the three tiers."""
    return EVENT_TIER_WEIGHTS.get(tier, EVENT_TIER_WEIGHTS[DEFAULT_EVENT_TIER])


def utcnow():
    return datetime.now(timezone.utc)


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


class ClientModel(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    # Full-access key: every endpoint, including PII reads (users/purchases/ratings) and
    # catalog/model writes. Server-to-server only - see app.py's PUBLIC_SCOPE_PATHS for
    # exactly what a "public" key below is restricted from. Nullable so an admin can
    # revoke it outright (see revoke_secret_key) without immediately issuing a
    # replacement - a client with no live secret key just can't authenticate as "secret"
    # scope until they self-serve a new one.
    secret_key_hash = Column(String, nullable=True, unique=True)
    secret_key_rotated_at = Column(DateTime(timezone=True), nullable=True)
    # Restricted key: read-only recommendations + interaction tracking only - safe to
    # embed in client-side JS. Nullable for the same reason as secret_key_hash, and also
    # because clients created before this two-tier split may not have generated one yet.
    public_key_hash = Column(String, nullable=True, unique=True)
    public_key_rotated_at = Column(DateTime(timezone=True), nullable=True)
    # See PLAN_LIMITS above - "free" (self-service default) or "unlimited" (the demo
    # client, or an admin-granted exemption). No billing integration yet.
    plan = Column(String, nullable=False, default=PLAN_FREE, server_default=PLAN_FREE)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    # Set only for clients created through the self-service website (Supabase auth) -
    # null for clients created directly via create_client.py (e.g. this demo's own key).
    supabase_user_id = Column(String, nullable=True, unique=True)
    # Kept in sync from the Supabase session JWT on every self-service /clients/me call
    # (see get_or_create_my_client) - not fetched live via the Supabase Admin API, so no
    # extra service-role secret is needed just to show "whose account is this" in admin.
    contact_email = Column(String, nullable=True)
    # Debounced, not updated on every single request (see touch_client_last_used) - good
    # enough for "is this key even alive" monitoring without a DB write per API call.
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class ApiUsageDailyModel(Base):
    """One row per (client, day), incremented on every authenticated request. Gives both
    a total-volume figure (sum across rows) and a real usage-over-time trend for the
    admin dashboard, at the cost of one lightweight upsert per request - cheaper than
    per-request logging, far more useful than a single blind counter."""
    __tablename__ = "api_usage_daily"

    client_id = Column(Integer, ForeignKey("clients.id"), primary_key=True)
    date = Column(Date, primary_key=True)
    request_count = Column(Integer, nullable=False, default=0)


class ProductModel(Base):
    __tablename__ = "products"

    client_id = Column(Integer, ForeignKey("clients.id"), primary_key=True)
    product_type = Column(String, primary_key=True)
    work_id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    genre_1 = Column(String, nullable=True)
    author = Column(String, nullable=True)
    year = Column(Integer, nullable=True)
    url = Column(String, nullable=True)
    price = Column(Float, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    # Semantic embedding of title+description+genre (all-MiniLM-L12-v2), computed at
    # ingestion time - nullable because it's only populated once a profile has content.
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)
    # Free-form item attributes as sent by the integrator (category, price, brand, ...) -
    # the public API's `properties`. genre_1/author/year/url/price above are the subset the
    # engine itself reads, derived from these at write time (see ingestion.derive_item_columns).
    # Null on rows written before this column existed: read as synthesized from the columns.
    properties = Column(JSONB, nullable=True)


class UserModel(Base):
    __tablename__ = "users"

    client_id = Column(Integer, ForeignKey("clients.id"), primary_key=True)
    product_type = Column(String, primary_key=True)
    user_id = Column(Integer, primary_key=True)
    user_gender = Column(String, nullable=True)
    user_age = Column(Integer, nullable=True)
    user_zip = Column(Integer, nullable=True)
    user_firstname = Column(String, nullable=True)
    user_lastname = Column(String, nullable=True)
    # Free-form user attributes (country, segment, ...) - the public API's `properties`.
    # The legacy columns above are kept for existing consumers; null on rows written before
    # this column existed (read as synthesized from those columns).
    properties = Column(JSONB, nullable=True)


class IdMapModel(Base):
    """Maps the identifiers a tenant sends (any string: "user_123", "SKU-NIKE-001", a UUID,
    a Shopify gid://...) to the dense per-(client, catalog) integers the engine works with
    internally. The ALS matrix is indexed directly by these integers, so they must stay
    small and dense - hence allocated here per (client_id, product_type, kind) instead of
    hashed. Rows written before this table existed are backfilled by the migration with
    external_id = str(internal_id), so no legacy id changes meaning.

    The primary key is the (tenant, external id) lookup every request does; the unique
    index is the reverse lookup used to translate engine output back to external ids."""
    __tablename__ = "id_map"

    client_id = Column(Integer, ForeignKey("clients.id"), primary_key=True)
    product_type = Column(String, primary_key=True)
    # "item" or "user" - see KIND_ITEM/KIND_USER
    kind = Column(String(8), primary_key=True)
    external_id = Column(String, primary_key=True)
    internal_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("uq_id_map_internal", "client_id", "product_type", "kind", "internal_id", unique=True),
    )


class RecommendationModel(Base):
    """Minimal trace of every recommendation call, so later events (impression, click,
    add_to_cart, purchase) that carry a recommendation_id can be attributed to it: CTR,
    conversion, attributed revenue, performance per strategy / per placement. Stores ids and
    context only - never item content, which stays in `products`. item_ids are the tenant's
    own external ids, in rank order, so the trace stays valid even if an item is later deleted."""
    __tablename__ = "recommendations"

    recommendation_id = Column(String, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    product_type = Column(String, nullable=False)
    user_id = Column(String, nullable=True)
    session_id = Column(String, nullable=True)
    # The anchor item (page being viewed) the recommendation was requested for, if any.
    item_id = Column(String, nullable=True)
    placement = Column(String, nullable=True)
    # The strategy that actually produced the items (after any fallback).
    strategy = Column(String, nullable=False)
    # "auto" (POST /getRec picked the strategy) or "explicit" (a strategy-specific endpoint).
    origin = Column(String(16), nullable=False, default="auto")
    item_ids = Column(JSONB, nullable=False)
    request_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_recommendations_client_created", "client_id", "created_at"),
        Index("ix_recommendations_client_placement", "client_id", "placement", "created_at"),
    )


class InteractionModel(Base):
    __tablename__ = "interactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    product_type = Column(String, nullable=False)
    work_id = Column(Integer, nullable=False)
    # Internal (id_map) user id - null for an anonymous visitor tracked by session_id only.
    # Collaborative training ignores null-user rows; popularity and session history use them.
    user_id = Column(Integer, nullable=True)
    event_type = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    # A row can carry both user_id and session_id: that is what lets a later job stitch an
    # anonymous session's history onto the user once they log in (not implemented yet -
    # `UPDATE ... SET user_id = ... WHERE session_id = ... AND user_id IS NULL` is all it takes).
    session_id = Column(String, nullable=True)
    # Client-supplied idempotency key, unique per tenant - see uq_interactions_event_id.
    event_id = Column(String, nullable=True)
    # Attribution to the recommendation call that surfaced the item (recommendations table).
    recommendation_id = Column(String, nullable=True)
    placement = Column(String, nullable=True)
    # Free-form event payload (price, currency, order_id, revenue, ...). Not read by the engine.
    properties = Column(JSONB, nullable=True)

    __table_args__ = (
        # No longer a fixed IN ('purchase', 'view') CheckConstraint - event_type is now a
        # tenant-defined vocabulary (see ClientEventTypeModel), open-ended the same way
        # product_type already is. Format validated at the API layer (EventTypePath's
        # regex in app.py), not enforced in the schema.
        Index("ix_interactions_lookup", "client_id", "product_type", "event_type", "user_id", "work_id"),
        Index("ix_interactions_client_event_time", "client_id", "event_type", "occurred_at"),
        Index(
            "ix_interactions_session", "client_id", "product_type", "session_id", "occurred_at",
            postgresql_where=text("session_id IS NOT NULL"),
        ),
        Index(
            "ix_interactions_recommendation", "recommendation_id",
            postgresql_where=text("recommendation_id IS NOT NULL"),
        ),
        Index(
            "uq_interactions_event_id", "client_id", "event_id", unique=True,
            postgresql_where=text("event_id IS NOT NULL"),
        ),
    )


class ModelVersionModel(Base):
    """One row per trained ALS model artifact, scoped by (client_id, product_type) - the
    same partition key used everywhere else. `is_active` marks which version is actually
    served; a training run is recorded here whether or not it ends up promoted, so a
    rejected candidate (worse precision@k than what's live) stays visible in the history
    instead of silently vanishing."""
    __tablename__ = "model_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    product_type = Column(String, nullable=False)
    trained_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    factors = Column(Integer, nullable=False)
    regularization = Column(Float, nullable=False)
    iterations = Column(Integer, nullable=False)
    precision_at_k = Column(Float, nullable=True)
    num_users = Column(Integer, nullable=True)
    num_items = Column(Integer, nullable=True)
    num_interactions = Column(Integer, nullable=True)
    file_path = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    # "manual" (client called /generateModel) or "auto" (auto-retrain) - see MANUAL/AUTO
    # above. Recorded per-run so the daily training quotas can be checked per kind, and
    # so the client dashboard can show which kind last updated the served model.
    triggered_by = Column(String(10), nullable=False, default=MANUAL)

    __table_args__ = (
        Index("ix_model_versions_lookup", "client_id", "product_type", "is_active"),
    )


class ClientEventTypeModel(Base):
    """A tenant's own vocabulary of interaction signals - not a fixed enum, since
    different verticals have different meaningful events (a library's "reservation", a
    video platform's "watch", an e-commerce site's "purchase"). `weight` is one of
    EVENT_TIER_WEIGHTS' three values, chosen by the tenant via a `tier` label
    (Faible/Moyen/Fort) rather than exposed as a raw ALS confidence number - see
    get_event_tier_weight(). `event_type` is the string used in interactions.event_type
    and in the POST /events/{event_type} URL."""
    __tablename__ = "client_event_types"

    client_id = Column(Integer, ForeignKey("clients.id"), primary_key=True)
    event_type = Column(String, primary_key=True)
    label = Column(String, nullable=False)
    weight = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


def init_db():
    Base.metadata.create_all(engine)
    # create_all only creates missing tables - it never alters an existing one, so columns,
    # indexes and backfills added since a database was first created come from the SQL
    # migrations (idempotent, additive, recorded in schema_migrations).
    from migrate import run_migrations
    run_migrations(engine)


# ---------------------------------------------------------------------------
# Clients (tenants)
# ---------------------------------------------------------------------------

def create_client(name: str) -> tuple[int, str, str]:
    """Returns (client_id, raw_secret_key, raw_public_key). Raw values are only ever
    available here - only their hashes are stored, so show/save them immediately, they
    can't be recovered later."""
    raw_secret_key, raw_public_key = generate_api_key(), generate_api_key()
    with SessionLocal() as session:
        client = ClientModel(
            name=name,
            secret_key_hash=hash_api_key(raw_secret_key), secret_key_rotated_at=utcnow(),
            public_key_hash=hash_api_key(raw_public_key), public_key_rotated_at=utcnow(),
        )
        session.add(client)
        session.commit()
        client_id = client.id
    seed_default_event_types(client_id)
    return client_id, raw_secret_key, raw_public_key


def ensure_client_id(client_id: int, name: str, plan: str = PLAN_UNLIMITED) -> None:
    """Used only to seed the fixed DEMO_CLIENT_ID row (id must be stable across runs,
    unlike create_client's autoincrement). Only a placeholder secret key is generated -
    the demo's real keys are provisioned separately via create_client.py so they can be
    printed and wired into the demo frontend. Defaults to the unlimited plan since this
    is meant for the demo/showcase client."""
    with SessionLocal() as session:
        existing = session.get(ClientModel, client_id)
        if existing is not None:
            return
        session.add(ClientModel(
            id=client_id, name=name, plan=plan,
            secret_key_hash=hash_api_key(secrets.token_urlsafe(32)), secret_key_rotated_at=utcnow(),
        ))
        # An explicit id doesn't advance the serial sequence: on a fresh database the very
        # next create_client() would be handed id 1 again and collide with this row.
        session.flush()
        session.execute(text(
            "SELECT setval(pg_get_serial_sequence('clients', 'id'), (SELECT GREATEST(MAX(id), 1) FROM clients))"
        ))
        session.commit()
    seed_default_event_types(client_id)


# ---------------------------------------------------------------------------
# Tenant-defined event types (see EVENT_TIER_WEIGHTS above)
# ---------------------------------------------------------------------------

def _event_type_row_to_dict(row: "ClientEventTypeModel") -> dict:
    # weight is only ever written via get_event_tier_weight(tier), so it always exactly
    # matches one of the three canonical values - safe to reverse-map for display.
    tier = next((t for t, w in EVENT_TIER_WEIGHTS.items() if w == row.weight), DEFAULT_EVENT_TIER)
    return {
        "event_type": row.event_type, "label": row.label, "tier": tier,
        "weight": row.weight, "created_at": row.created_at,
    }


def seed_default_event_types(client_id: int) -> None:
    """Every client starts with the officially supported event types (DEFAULT_EVENT_TYPES).
    "purchase" (fort) and "view" (faible) are the two this system always had, at their
    pre-existing exact weights, so nothing changes for them. A tenant only needs to touch
    this if their vertical needs something else (a reservation, a watch event, ...)."""
    for event_type, label, tier in DEFAULT_EVENT_TYPES:
        upsert_client_event_type(client_id, event_type, label, tier)


def get_client_event_types(client_id: int) -> list[dict]:
    with SessionLocal() as session:
        rows = (
            session.query(ClientEventTypeModel)
            .filter_by(client_id=client_id)
            .order_by(ClientEventTypeModel.created_at)
            .all()
        )
        return [_event_type_row_to_dict(r) for r in rows]


def upsert_client_event_type(client_id: int, event_type: str, label: str, tier: str) -> dict:
    weight = get_event_tier_weight(tier)
    with SessionLocal() as session:
        obj = session.get(ClientEventTypeModel, {"client_id": client_id, "event_type": event_type})
        if obj is None:
            obj = ClientEventTypeModel(client_id=client_id, event_type=event_type)
            session.add(obj)
        obj.label = label
        obj.weight = weight
        session.commit()
        return _event_type_row_to_dict(obj)


def delete_client_event_type(client_id: int, event_type: str) -> bool:
    with SessionLocal() as session:
        obj = session.get(ClientEventTypeModel, {"client_id": client_id, "event_type": event_type})
        if obj is None:
            return False
        session.delete(obj)
        session.commit()
        return True


def get_client_event_type_weights(client_id: int) -> dict[str, float]:
    """Used by training/popularity code to weight each interaction row by its event
    type. An event_type present in `interactions` but never registered here shouldn't
    normally happen (ingestion auto-registers on first use - see
    record_interaction_event in app.py), but callers should still use dict.get with the
    default-tier weight as a fallback rather than assume every key exists."""
    with SessionLocal() as session:
        rows = session.query(ClientEventTypeModel).filter_by(client_id=client_id).all()
        return {r.event_type: r.weight for r in rows}


def get_dominant_event_type(client_id: int) -> Optional[dict]:
    """This client's strongest-tier event type, used to phrase a generic "Populaire (N
    <label>)" explanation in place of the old hardcoded "achats" text - ties broken by
    whichever was registered first (purchase/view are always seeded first, so a client
    that hasn't customized anything gets "purchase" here, same as before this
    generalization)."""
    types = get_client_event_types(client_id)
    if not types:
        return None
    return max(types, key=lambda t: (t["weight"], -t["created_at"].timestamp()))


def get_client_and_scope_by_api_key(raw_key: str) -> Optional[tuple[int, str]]:
    """Resolves a raw X-API-Key to (client_id, scope), scope being "secret" (full
    access) or "public" (restricted - see PUBLIC_SCOPE_PATHS in app.py). Checked as two
    separate lookups rather than one query with an OR, since a client's two keys are
    independent unique values that can each be rotated on their own schedule."""
    key_hash = hash_api_key(raw_key)
    with SessionLocal() as session:
        client = session.query(ClientModel).filter_by(secret_key_hash=key_hash, is_active=True).first()
        if client:
            return client.id, "secret"
        client = session.query(ClientModel).filter_by(public_key_hash=key_hash, is_active=True).first()
        if client:
            return client.id, "public"
        return None


def get_client_plan(client_id: int) -> str:
    """Used at every quota enforcement site instead of the old `client_id ==
    DEMO_CLIENT_ID` check - falls back to the free plan if the client row is somehow
    gone, same fail-closed reasoning as get_plan_limits()."""
    with SessionLocal() as session:
        client = session.get(ClientModel, client_id)
        return client.plan if client else PLAN_FREE


def set_client_plan(client_id: int, plan: str) -> bool:
    with SessionLocal() as session:
        result = session.query(ClientModel).filter_by(id=client_id).update({"plan": plan})
        session.commit()
        return result > 0


# Debounces last_used_at writes so a busy client doesn't cause one on every single
# request - refreshed at most once per this many seconds per client.
_LAST_USED_DEBOUNCE_SECONDS = 60
_last_used_write_cache: dict[int, datetime] = {}


def touch_client_usage(client_id: int) -> None:
    """Called (as a background task, off the request's critical path - see
    get_current_client_id) on every authenticated recsys API request: increments
    today's usage counter and refreshes last_used_at, so /admin can show real
    "is this key alive" and volume-over-time signals instead of nothing at all."""
    now = utcnow()
    with SessionLocal() as session:
        session.execute(
            text("""
                INSERT INTO api_usage_daily (client_id, date, request_count)
                VALUES (:client_id, :date, 1)
                ON CONFLICT (client_id, date)
                DO UPDATE SET request_count = api_usage_daily.request_count + 1
            """),
            {"client_id": client_id, "date": now.date()},
        )

        last_write = _last_used_write_cache.get(client_id)
        if last_write is None or (now - last_write).total_seconds() > _LAST_USED_DEBOUNCE_SECONDS:
            session.query(ClientModel).filter_by(id=client_id).update({"last_used_at": now})
            _last_used_write_cache[client_id] = now

        session.commit()


def list_all_clients_with_usage() -> list[dict]:
    """Admin-only view: every client, when created, when last used, and total request
    volume (sum across api_usage_daily) - the whole point of tracking any of this."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT
                c.id, c.name, c.contact_email, c.is_active, c.created_at, c.last_used_at, c.plan,
                c.supabase_user_id IS NOT NULL AS is_self_service,
                c.secret_key_hash IS NOT NULL AS has_secret_key, c.secret_key_rotated_at,
                c.public_key_hash IS NOT NULL AS has_public_key, c.public_key_rotated_at,
                COALESCE(SUM(u.request_count), 0) AS total_requests
            FROM clients c
            LEFT JOIN api_usage_daily u ON u.client_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC
        """)).mappings().all()
        return [dict(r) for r in rows]


def get_client_admin_row(client_id: int) -> Optional[dict]:
    """Same shape as one row of list_all_clients_with_usage(), scoped to a single client -
    used by the admin detail panel and by the admin regenerate/rename endpoints, which
    need the freshly-updated row without re-fetching the whole client list."""
    with SessionLocal() as session:
        row = session.execute(text("""
            SELECT
                c.id, c.name, c.contact_email, c.is_active, c.created_at, c.last_used_at, c.plan,
                c.supabase_user_id IS NOT NULL AS is_self_service,
                c.secret_key_hash IS NOT NULL AS has_secret_key, c.secret_key_rotated_at,
                c.public_key_hash IS NOT NULL AS has_public_key, c.public_key_rotated_at,
                COALESCE((SELECT SUM(request_count) FROM api_usage_daily WHERE client_id = c.id), 0) AS total_requests
            FROM clients c
            WHERE c.id = :client_id
        """), {"client_id": client_id}).mappings().first()
        return dict(row) if row else None


def rename_client(client_id: int, name: str) -> bool:
    with SessionLocal() as session:
        result = session.query(ClientModel).filter_by(id=client_id).update({"name": name})
        session.commit()
        return result > 0


def get_client_usage_by_day(client_id: int, days: int = 30) -> list[dict]:
    with SessionLocal() as session:
        rows = session.execute(
            text("""
                SELECT date, request_count FROM api_usage_daily
                WHERE client_id = :client_id AND date >= CURRENT_DATE - :days
                ORDER BY date
            """),
            {"client_id": client_id, "days": days},
        ).mappings().all()
        return [dict(r) for r in rows]


def get_client_by_supabase_user_id(supabase_user_id: str) -> Optional[dict]:
    with SessionLocal() as session:
        client = session.query(ClientModel).filter_by(supabase_user_id=supabase_user_id).first()
        if client is None:
            return None
        return {
            "id": client.id, "name": client.name,
            "has_public_key": client.public_key_hash is not None,
            "secret_key_rotated_at": client.secret_key_rotated_at,
            "public_key_rotated_at": client.public_key_rotated_at,
        }


def set_client_contact_email(client_id: int, email: Optional[str]) -> None:
    """Keeps clients.contact_email in sync with the Supabase account's current email -
    called on every self-service /clients/me call (see get_or_create_my_client), not
    just at creation, so it self-heals for rows created before this field existed and
    stays correct if the account's email ever changes."""
    if not email:
        return
    with SessionLocal() as session:
        session.query(ClientModel).filter_by(id=client_id).update({"contact_email": email})
        session.commit()


def create_client_for_supabase_user(name: str, supabase_user_id: str, email: Optional[str] = None) -> tuple[int, str, str]:
    """Same contract as create_client() - the raw keys are only ever available here."""
    raw_secret_key, raw_public_key = generate_api_key(), generate_api_key()
    with SessionLocal() as session:
        client = ClientModel(
            name=name,
            secret_key_hash=hash_api_key(raw_secret_key), secret_key_rotated_at=utcnow(),
            public_key_hash=hash_api_key(raw_public_key), public_key_rotated_at=utcnow(),
            supabase_user_id=supabase_user_id, contact_email=email,
        )
        session.add(client)
        session.commit()
        client_id = client.id
    seed_default_event_types(client_id)
    return client_id, raw_secret_key, raw_public_key


def regenerate_secret_key(client_id: int) -> str:
    """Invalidates the current secret key and issues a new one - the only way to recover
    from a lost key, since the raw value is never stored (only its hash)."""
    raw_key = generate_api_key()
    with SessionLocal() as session:
        session.query(ClientModel).filter_by(id=client_id).update({
            "secret_key_hash": hash_api_key(raw_key), "secret_key_rotated_at": utcnow(),
        })
        session.commit()
    return raw_key


def regenerate_public_key(client_id: int) -> str:
    """Same as regenerate_secret_key, for the restricted public key - independent
    rotation, since the two keys are meant to live in different places (server env vs.
    client-side JS) with different exposure risk."""
    raw_key = generate_api_key()
    with SessionLocal() as session:
        session.query(ClientModel).filter_by(id=client_id).update({
            "public_key_hash": hash_api_key(raw_key), "public_key_rotated_at": utcnow(),
        })
        session.commit()
    return raw_key


# ---------------------------------------------------------------------------
# Admin operations: revoke/disable/delete - all destructive and gated behind
# get_current_admin_user_id in app.py, never reachable from the self-service endpoints.
# ---------------------------------------------------------------------------

def revoke_secret_key(client_id: int) -> None:
    """Kills the current secret key with no replacement - unlike regenerate_secret_key,
    there's no raw value to hand back here (this is called by an admin, not the client
    who'd actually use it), so the point is purely to cut off access immediately (e.g.
    incident response on a leaked key). The client can self-serve a new one afterwards
    from their own account page."""
    with SessionLocal() as session:
        session.query(ClientModel).filter_by(id=client_id).update({"secret_key_hash": None})
        session.commit()


def revoke_public_key(client_id: int) -> None:
    """Same as revoke_secret_key, for the restricted public key."""
    with SessionLocal() as session:
        session.query(ClientModel).filter_by(id=client_id).update({"public_key_hash": None})
        session.commit()


def set_client_active(client_id: int, is_active: bool) -> bool:
    """Suspends or restores a client - immediately blocks (or restores) both keys at
    once, since get_client_and_scope_by_api_key requires is_active=True regardless of
    which key matched. Returns False if the client doesn't exist."""
    with SessionLocal() as session:
        result = session.query(ClientModel).filter_by(id=client_id).update({"is_active": is_active})
        session.commit()
        return result > 0


def delete_client(client_id: int) -> bool:
    """Permanently deletes a client and everything scoped to it (catalog, users,
    interactions, id mappings, recommendation traces, model version history, usage counters,
    event type definitions) - there
    is no undo. Rows are
    deleted table-by-table in application code rather than via an ON DELETE CASCADE
    constraint, so the full blast radius stays visible here instead of hidden in a
    schema-level constraint. Trained model artifact files on disk (model_versions.
    file_path) are not deleted - orphaned but harmless. Returns False if the client
    doesn't exist."""
    with SessionLocal() as session:
        client = session.get(ClientModel, client_id)
        if client is None:
            return False
        session.query(ApiUsageDailyModel).filter_by(client_id=client_id).delete()
        session.query(InteractionModel).filter_by(client_id=client_id).delete()
        session.query(ProductModel).filter_by(client_id=client_id).delete()
        session.query(UserModel).filter_by(client_id=client_id).delete()
        session.query(ModelVersionModel).filter_by(client_id=client_id).delete()
        session.query(ClientEventTypeModel).filter_by(client_id=client_id).delete()
        session.query(IdMapModel).filter_by(client_id=client_id).delete()
        session.query(RecommendationModel).filter_by(client_id=client_id).delete()
        session.delete(client)
        session.commit()
        return True


# ---------------------------------------------------------------------------
# Reads (return plain pandas DataFrames so the rest of the pipeline, written
# against pandas, doesn't need to change)
# ---------------------------------------------------------------------------

def fetch_products(
    product_type: str,
    work_id: Optional[int] = None,
    count: Optional[int] = None,
    client_id: int = DEMO_CLIENT_ID,
    offset: Optional[int] = None,
) -> pd.DataFrame:
    query = select(ProductModel).where(
        ProductModel.client_id == client_id,
        ProductModel.product_type == product_type,
    )
    if work_id is not None:
        query = query.where(ProductModel.work_id == work_id)
    query = query.order_by(ProductModel.work_id)
    if offset:
        query = query.offset(offset)
    if count is not None:
        query = query.limit(count)

    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    columns = ["work_id", "title", "description", "genre_1", "author", "year", "url", "price"]
    return df[columns] if not df.empty else pd.DataFrame(columns=columns)


def fetch_users(
    product_type: str,
    user_id: Optional[int] = None,
    count: Optional[int] = None,
    client_id: int = DEMO_CLIENT_ID,
    offset: Optional[int] = None,
) -> pd.DataFrame:
    query = select(UserModel).where(
        UserModel.client_id == client_id,
        UserModel.product_type == product_type,
    )
    if user_id is not None:
        query = query.where(UserModel.user_id == user_id)
    query = query.order_by(UserModel.user_id)
    if offset:
        query = query.offset(offset)
    if count is not None:
        query = query.limit(count)

    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    columns = ["user_id", "user_gender", "user_age", "user_zip", "user_firstname", "user_lastname"]
    return df[columns] if not df.empty else pd.DataFrame(columns=columns)


def fetch_interactions(
    product_type: str,
    event_type: str,
    user_id: Optional[int] = None,
    count: Optional[int] = None,
    since_days: Optional[int] = None,
    client_id: int = DEMO_CLIENT_ID,
) -> pd.DataFrame:
    """Per-user rows only: anonymous (session-only) events have no user_id and are left out
    here - every caller (the users/purchases/page-views exports, the "already bought"
    exclusion) is per user. Session history is read by get_recent_viewed_work_ids_for_session."""
    query = select(InteractionModel).where(
        InteractionModel.client_id == client_id,
        InteractionModel.product_type == product_type,
        InteractionModel.event_type == event_type,
        InteractionModel.user_id.isnot(None),
    )
    if user_id is not None:
        query = query.where(InteractionModel.user_id == user_id)
    if since_days is not None:
        cutoff = utcnow() - timedelta(days=since_days)
        query = query.where(InteractionModel.occurred_at >= cutoff)
    query = query.order_by(InteractionModel.occurred_at.desc())
    if count is not None:
        query = query.limit(count)

    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    columns = ["user_id", "work_id", "quantity", "occurred_at"]
    return df[columns] if not df.empty else pd.DataFrame(columns=columns)


def fetch_all_interactions(product_type: str, client_id: int = DEMO_CLIENT_ID) -> pd.DataFrame:
    """Every interaction row for this client/product_type, across all event types -
    unlike fetch_interactions, which filters to one type the caller already knows, this
    includes `event_type` in the output since callers (training/popularity - see
    modelData.py) need to weight each row by its own type."""
    query = select(InteractionModel).where(
        InteractionModel.client_id == client_id,
        InteractionModel.product_type == product_type,
    )
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    columns = ["user_id", "work_id", "event_type", "quantity", "occurred_at"]
    return df[columns] if not df.empty else pd.DataFrame(columns=columns)


def get_recent_viewed_work_ids(
    client_id: int, product_type: str, user_id: int, limit: int = 10,
) -> list[int]:
    """Most recently viewed work_ids for this user, most recent last - same order the
    client-side SessionTracker (localStorage) keeps for anonymous visitors, but sourced
    from persisted history so a logged-in user's "for you" recs survive across devices
    and sessions instead of living only in one browser."""
    return _recent_viewed_work_ids(client_id, product_type, limit, user_id=user_id)


def get_recent_viewed_work_ids_for_session(
    client_id: int, product_type: str, session_id: str, limit: int = 10,
) -> list[int]:
    """Same as get_recent_viewed_work_ids, for an anonymous visitor identified only by the
    session_id their view events carried - so a visitor's own tracked browsing feeds their
    recommendations without the client having to resend the viewed list on every call."""
    return _recent_viewed_work_ids(client_id, product_type, limit, session_id=session_id)


def _recent_viewed_work_ids(
    client_id: int, product_type: str, limit: int,
    user_id: Optional[int] = None, session_id: Optional[str] = None,
) -> list[int]:
    with SessionLocal() as session:
        query = session.query(InteractionModel.work_id, InteractionModel.occurred_at).filter_by(
            client_id=client_id, product_type=product_type, event_type=VIEW,
        )
        if user_id is not None:
            query = query.filter(InteractionModel.user_id == user_id)
        if session_id is not None:
            query = query.filter(InteractionModel.session_id == session_id)
        rows = (
            query.order_by(InteractionModel.occurred_at.desc())
            .limit(limit * 3)  # over-fetch before de-duping, since repeats collapse
            .all()
        )
        seen = set()
        ordered_recent_first = []
        for r in rows:
            if r.work_id not in seen:
                seen.add(r.work_id)
                ordered_recent_first.append(r.work_id)
            if len(ordered_recent_first) >= limit:
                break
        return list(reversed(ordered_recent_first))


def count_interactions_since(
    client_id: int, product_type: str, since: Optional[datetime] = None,
) -> int:
    """How many signal-carrying interaction rows exist for this client/product_type since a
    given timestamp - used to decide whether enough new signal has accumulated to justify an
    automatic retrain, instead of a blind time-based cron. Rows of zero-weight event types
    (impressions, remove_from_cart) and anonymous rows are not signal for the ALS model, so
    they don't count - otherwise a busy widget's impressions alone would fire a retrain."""
    zero_weight_types = (
        select(ClientEventTypeModel.event_type)
        .where(ClientEventTypeModel.client_id == client_id, ClientEventTypeModel.weight <= 0)
    )
    with SessionLocal() as session:
        query = session.query(InteractionModel).filter(
            InteractionModel.client_id == client_id,
            InteractionModel.product_type == product_type,
            InteractionModel.user_id.isnot(None),
            InteractionModel.event_type.notin_(zero_weight_types),
        )
        if since is not None:
            query = query.filter(InteractionModel.occurred_at >= since)
        return query.count()


def count_products_for_client(client_id: int) -> int:
    """Total catalog size for this client, across all its product_types - the free-tier
    product cap is an account-wide limit, not a per-catalog one."""
    with SessionLocal() as session:
        return session.query(ProductModel).filter_by(client_id=client_id).count()


def list_product_types_for_client(client_id: int) -> list[str]:
    """Every product_type this client has pushed a catalog profile for - product_type is
    free-form text the client chooses (see ProductType in app.py), not a fixed enum, so
    this is how the self-service dashboard knows which catalog(s) to show a quota card
    for. Scoped to ProductModel only (not InteractionModel) - a client with events but no
    catalog profile yet has nothing model-status-relevant to show."""
    with SessionLocal() as session:
        rows = session.query(ProductModel.product_type).filter_by(client_id=client_id).distinct().all()
        return [r[0] for r in rows]


def list_catalogs_for_client(client_id: int) -> list[str]:
    """Every catalog (product_type) this client has anything in - items OR events - so a request
    that omits data_product_type can tell "one catalog, use it" from "several, be explicit".
    Unlike list_product_types_for_client (items only, for the dashboard), an events-only client
    (collaborative filtering with no catalog pushed) counts too."""
    with SessionLocal() as session:
        rows = session.execute(text(
            "SELECT product_type FROM products WHERE client_id = :c "
            "UNION SELECT product_type FROM interactions WHERE client_id = :c"
        ), {"c": client_id}).all()
        return sorted(r[0] for r in rows)


def product_exists(client_id: int, product_type: str, work_id: int) -> bool:
    with SessionLocal() as session:
        return session.get(ProductModel, {
            "client_id": client_id, "product_type": product_type, "work_id": work_id,
        }) is not None


def count_trainings_today(client_id: int, product_type: str, triggered_by: str) -> int:
    """How many training runs of this kind (MANUAL/AUTO) were already recorded today for
    this client/product_type - used to enforce the free-tier daily training quotas."""
    with SessionLocal() as session:
        start_of_day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return session.query(ModelVersionModel).filter(
            ModelVersionModel.client_id == client_id,
            ModelVersionModel.product_type == product_type,
            ModelVersionModel.triggered_by == triggered_by,
            ModelVersionModel.trained_at >= start_of_day,
        ).count()


# ---------------------------------------------------------------------------
# Public <-> internal id mapping (see IdMapModel)
# ---------------------------------------------------------------------------

KIND_ITEM = "item"
KIND_USER = "user"


def _advisory_lock_key(client_id: int, product_type: str, kind: str) -> int:
    """Stable 63-bit key for pg_advisory_xact_lock, one per (client, catalog, kind) - so two
    concurrent requests introducing brand-new ids in the same namespace allocate their
    internal ints one after the other instead of both computing the same MAX+1."""
    digest = zlib.crc32(f"{client_id}:{product_type}:{kind}".encode("utf-8"))
    return (client_id << 32) | digest


def resolve_internal_ids(
    client_id: int, product_type: str, kind: str, external_ids: list[str], create: bool = False,
) -> dict[str, int]:
    """external id -> engine-internal int, for every id in `external_ids` that is known (or,
    with create=True, that could be allocated - so the result then covers all of them).
    Reads never create: a recommendation request mentioning an id we've never seen must not
    leave a mapping behind."""
    wanted = list(dict.fromkeys(external_ids))
    if not wanted:
        return {}

    with SessionLocal() as session:
        found = _lookup_internal_ids(session, client_id, product_type, kind, wanted)
        missing = [e for e in wanted if e not in found]
        if not missing or not create:
            return found

        # Serialize allocation for this namespace, then re-read: another transaction may
        # have created some of the "missing" ids between the lookup above and the lock.
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _advisory_lock_key(client_id, product_type, kind)})
        found = _lookup_internal_ids(session, client_id, product_type, kind, wanted)
        missing = [e for e in wanted if e not in found]
        if missing:
            highest = session.execute(
                text(
                    "SELECT COALESCE(MAX(internal_id), -1) FROM id_map "
                    "WHERE client_id = :c AND product_type = :p AND kind = :k"
                ),
                {"c": client_id, "p": product_type, "k": kind},
            ).scalar_one()
            for offset, external_id in enumerate(missing, start=1):
                found[external_id] = highest + offset
                session.add(IdMapModel(
                    client_id=client_id, product_type=product_type, kind=kind,
                    external_id=external_id, internal_id=highest + offset,
                ))
            session.commit()
        else:
            session.rollback()  # ends the transaction, releasing the advisory lock
        return found


def _lookup_internal_ids(session, client_id: int, product_type: str, kind: str, external_ids: list[str]) -> dict[str, int]:
    rows = session.query(IdMapModel.external_id, IdMapModel.internal_id).filter(
        IdMapModel.client_id == client_id, IdMapModel.product_type == product_type,
        IdMapModel.kind == kind, IdMapModel.external_id.in_(external_ids),
    ).all()
    return {r.external_id: r.internal_id for r in rows}


def resolve_external_ids(client_id: int, product_type: str, kind: str, internal_ids: list[int]) -> dict[int, str]:
    """engine-internal int -> the id the tenant knows it by. An internal id with no mapping
    (shouldn't happen after the migration's backfill, but a row inserted by a direct-DB
    script could) falls back to str(internal_id) in callers - which is exactly what the
    backfill would have produced."""
    wanted = list({int(i) for i in internal_ids})
    if not wanted:
        return {}
    with SessionLocal() as session:
        rows = session.query(IdMapModel.internal_id, IdMapModel.external_id).filter(
            IdMapModel.client_id == client_id, IdMapModel.product_type == product_type,
            IdMapModel.kind == kind, IdMapModel.internal_id.in_(wanted),
        ).all()
        return {r.internal_id: r.external_id for r in rows}


# ---------------------------------------------------------------------------
# Writes (ingestion)
# ---------------------------------------------------------------------------

def upsert_product_profile(
    product_type: str,
    work_id: int,
    title: str,
    description: Optional[str] = None,
    genre_1: Optional[str] = None,
    author: Optional[str] = None,
    year: Optional[int] = None,
    url: Optional[str] = None,
    price: Optional[float] = None,
    client_id: int = DEMO_CLIENT_ID,
    properties: Optional[dict[str, Any]] = None,
) -> bool:
    """Returns True if the product was newly created, False if an existing one was updated."""
    with SessionLocal() as session:
        obj = session.get(ProductModel, {"client_id": client_id, "product_type": product_type, "work_id": work_id})
        created = obj is None
        if obj is None:
            obj = ProductModel(client_id=client_id, product_type=product_type, work_id=work_id)
            session.add(obj)

        obj.title = title
        obj.description = description
        obj.genre_1 = genre_1
        obj.author = author
        obj.year = year
        obj.url = url
        obj.price = price
        obj.properties = properties
        obj.updated_at = utcnow()

        session.commit()
        return created


def delete_product_profile(product_type: str, work_id: int, client_id: int = DEMO_CLIENT_ID) -> bool:
    with SessionLocal() as session:
        obj = session.get(ProductModel, {"client_id": client_id, "product_type": product_type, "work_id": work_id})
        if obj is None:
            return False
        session.delete(obj)
        session.commit()
        return True


def fetch_item_properties(client_id: int, product_type: str, work_ids: list[int]) -> dict[int, Optional[dict]]:
    """work_id -> stored `properties` JSON (None for a row written before that column
    existed) - kept out of fetch_products' DataFrame on purpose: the content-based pipeline
    indexes that frame's columns by position (see get_data_similarities)."""
    wanted = list({int(w) for w in work_ids})
    if not wanted:
        return {}
    with SessionLocal() as session:
        rows = session.query(ProductModel.work_id, ProductModel.properties).filter(
            ProductModel.client_id == client_id, ProductModel.product_type == product_type,
            ProductModel.work_id.in_(wanted),
        ).all()
        return {r.work_id: r.properties for r in rows}


def count_products_in_catalog(client_id: int, product_type: str) -> int:
    with SessionLocal() as session:
        return session.query(ProductModel).filter_by(client_id=client_id, product_type=product_type).count()


def insert_interactions(rows: list[dict[str, Any]]) -> int:
    """Bulk insert; returns how many rows were actually written. A row whose (client_id,
    event_id) already exists is skipped silently (ON CONFLICT DO NOTHING against the partial
    unique index) - that is the whole idempotency mechanism, and it also covers the same
    event_id appearing twice inside one batch."""
    if not rows:
        return 0
    now = utcnow()
    values = [{**row, "occurred_at": row.get("occurred_at") or now} for row in rows]
    statement = (
        pg_insert(InteractionModel)
        .values(values)
        .on_conflict_do_nothing(
            index_elements=[InteractionModel.client_id, InteractionModel.event_id],
            index_where=text("event_id IS NOT NULL"),
        )
        .returning(InteractionModel.id)
    )
    with SessionLocal() as session:
        inserted = len(session.execute(statement).all())
        session.commit()
        return inserted


def insert_interaction(
    product_type: str,
    work_id: int,
    user_id: Optional[int],
    event_type: str,
    quantity: int = 1,
    occurred_at: Optional[datetime] = None,
    client_id: int = DEMO_CLIENT_ID,
    session_id: Optional[str] = None,
    event_id: Optional[str] = None,
    recommendation_id: Optional[str] = None,
    placement: Optional[str] = None,
    properties: Optional[dict[str, Any]] = None,
) -> bool:
    """Returns False if this event_id was already recorded (nothing written)."""
    return insert_interactions([{
        "client_id": client_id, "product_type": product_type, "work_id": work_id,
        "user_id": user_id, "event_type": event_type, "quantity": quantity,
        "occurred_at": occurred_at, "session_id": session_id, "event_id": event_id,
        "recommendation_id": recommendation_id, "placement": placement, "properties": properties,
    }]) == 1


def upsert_user(
    product_type: str,
    user_id: int,
    user_gender: Optional[str] = None,
    user_age: Optional[int] = None,
    user_zip: Optional[int] = None,
    user_firstname: Optional[str] = None,
    user_lastname: Optional[str] = None,
    client_id: int = DEMO_CLIENT_ID,
    properties: Optional[dict[str, Any]] = None,
) -> bool:
    """Returns True if the user row was newly created, False if an existing one was updated."""
    with SessionLocal() as session:
        obj = session.get(UserModel, {"client_id": client_id, "product_type": product_type, "user_id": user_id})
        created = obj is None
        if obj is None:
            obj = UserModel(client_id=client_id, product_type=product_type, user_id=user_id)
            session.add(obj)

        obj.user_gender = user_gender
        obj.user_age = user_age
        obj.user_zip = user_zip
        obj.user_firstname = user_firstname
        obj.user_lastname = user_lastname
        obj.properties = properties

        session.commit()
        return created


def fetch_user_rows(
    client_id: int, product_type: str, user_id: Optional[int] = None,
    limit: Optional[int] = None, offset: Optional[int] = None,
) -> list[dict[str, Any]]:
    """User profile rows (internal user_id + every profile column incl. `properties`), ordered
    by internal id. The public users API translates internal ids back to external ones."""
    with SessionLocal() as session:
        query = session.query(UserModel).filter_by(client_id=client_id, product_type=product_type)
        if user_id is not None:
            query = query.filter(UserModel.user_id == user_id)
        query = query.order_by(UserModel.user_id)
        if offset:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        return [
            {
                "user_id": r.user_id, "user_gender": r.user_gender, "user_age": r.user_age,
                "user_zip": r.user_zip, "user_firstname": r.user_firstname,
                "user_lastname": r.user_lastname, "properties": r.properties,
            }
            for r in query.all()
        ]


def count_users_in_catalog(client_id: int, product_type: str) -> int:
    with SessionLocal() as session:
        return session.query(UserModel).filter_by(client_id=client_id, product_type=product_type).count()


def delete_user_data(client_id: int, product_type: str, user_id: int) -> bool:
    """Erases a user: profile, every interaction they generated, and their id mapping (right
    to erasure - an integrator deleting a user expects them gone, not orphaned events still
    steering recommendations). Returns False if there was nothing at all for this user."""
    with SessionLocal() as session:
        profile = session.get(UserModel, {"client_id": client_id, "product_type": product_type, "user_id": user_id})
        interactions_deleted = session.query(InteractionModel).filter_by(
            client_id=client_id, product_type=product_type, user_id=user_id,
        ).delete()
        mapping_deleted = session.query(IdMapModel).filter_by(
            client_id=client_id, product_type=product_type, kind=KIND_USER, internal_id=user_id,
        ).delete()
        if profile is not None:
            session.delete(profile)
        session.commit()
        return profile is not None or interactions_deleted > 0 or mapping_deleted > 0


def count_user_signal_interactions(client_id: int, product_type: str, user_id: int) -> int:
    """How many signal-carrying (non-zero-weight) interactions this user has - what tells the
    automatic strategy selection whether collaborative filtering has anything to work with."""
    zero_weight_types = (
        select(ClientEventTypeModel.event_type)
        .where(ClientEventTypeModel.client_id == client_id, ClientEventTypeModel.weight <= 0)
    )
    with SessionLocal() as session:
        return session.query(InteractionModel).filter(
            InteractionModel.client_id == client_id,
            InteractionModel.product_type == product_type,
            InteractionModel.user_id == user_id,
            InteractionModel.event_type.notin_(zero_weight_types),
        ).count()


# ---------------------------------------------------------------------------
# Recommendation traces (see RecommendationModel)
# ---------------------------------------------------------------------------

def store_recommendation(
    recommendation_id: str, client_id: int, product_type: str, strategy: str, origin: str,
    item_ids: list[str], user_id: Optional[str] = None, session_id: Optional[str] = None,
    item_id: Optional[str] = None, placement: Optional[str] = None, request_id: Optional[str] = None,
) -> None:
    with SessionLocal() as session:
        session.add(RecommendationModel(
            recommendation_id=recommendation_id, client_id=client_id, product_type=product_type,
            user_id=user_id, session_id=session_id, item_id=item_id, placement=placement,
            strategy=strategy, origin=origin, item_ids=item_ids, request_id=request_id,
        ))
        session.commit()


def get_recommendation(client_id: int, recommendation_id: str) -> Optional[dict[str, Any]]:
    with SessionLocal() as session:
        row = session.query(RecommendationModel).filter_by(
            client_id=client_id, recommendation_id=recommendation_id,
        ).first()
        if row is None:
            return None
        return {
            "recommendation_id": row.recommendation_id, "product_type": row.product_type,
            "user_id": row.user_id, "session_id": row.session_id, "item_id": row.item_id,
            "placement": row.placement, "strategy": row.strategy, "origin": row.origin,
            "item_ids": row.item_ids, "created_at": row.created_at,
        }


# ---------------------------------------------------------------------------
# Model version registry (governance: which trained model is actually served)
# ---------------------------------------------------------------------------

def record_model_version(
    client_id: int,
    product_type: str,
    file_path: str,
    factors: int,
    regularization: float,
    iterations: int,
    precision_at_k: Optional[float] = None,
    num_users: Optional[int] = None,
    num_items: Optional[int] = None,
    num_interactions: Optional[int] = None,
    triggered_by: str = MANUAL,
) -> int:
    """Records a training run. Always inserted, whether or not it later gets promoted -
    a rejected candidate stays in the history instead of disappearing."""
    with SessionLocal() as session:
        row = ModelVersionModel(
            client_id=client_id, product_type=product_type, file_path=file_path,
            factors=factors, regularization=regularization, iterations=iterations,
            precision_at_k=precision_at_k, num_users=num_users, num_items=num_items,
            num_interactions=num_interactions, is_active=False, triggered_by=triggered_by,
        )
        session.add(row)
        session.commit()
        return row.id


def update_model_version_file_path(version_id: int, file_path: str) -> None:
    with SessionLocal() as session:
        session.query(ModelVersionModel).filter_by(id=version_id).update({"file_path": file_path})
        session.commit()


def _model_version_row_to_dict(row: ModelVersionModel) -> dict:
    return {
        "id": row.id, "trained_at": row.trained_at, "factors": row.factors,
        "regularization": row.regularization, "iterations": row.iterations,
        "precision_at_k": row.precision_at_k, "num_users": row.num_users,
        "num_items": row.num_items, "num_interactions": row.num_interactions,
        "file_path": row.file_path, "is_active": row.is_active,
        "triggered_by": row.triggered_by,
    }


def get_active_model_version(client_id: int, product_type: str) -> Optional[dict]:
    with SessionLocal() as session:
        row = session.query(ModelVersionModel).filter_by(
            client_id=client_id, product_type=product_type, is_active=True,
        ).first()
        return _model_version_row_to_dict(row) if row is not None else None


def promote_model_version(version_id: int, client_id: int, product_type: str) -> None:
    """Marks this version active and demotes any previously-active version for the same
    (client_id, product_type) - exactly one active version per pair at a time."""
    with SessionLocal() as session:
        session.query(ModelVersionModel).filter_by(
            client_id=client_id, product_type=product_type, is_active=True,
        ).update({"is_active": False})
        session.query(ModelVersionModel).filter_by(id=version_id).update({"is_active": True})
        session.commit()


def update_product_embedding(client_id: int, product_type: str, work_id: int, embedding: list[float]) -> None:
    with SessionLocal() as session:
        session.query(ProductModel).filter_by(
            client_id=client_id, product_type=product_type, work_id=work_id,
        ).update({"embedding": embedding})
        session.commit()


def find_similar_by_embedding(client_id: int, product_type: str, work_id: int, count: int = 4) -> list[dict]:
    """Nearest neighbors by cosine distance in embedding space (pgvector `<=>` operator),
    scoped to this client's own catalog - exact search at this catalog size, an ANN
    index (HNSW) can be added later without changing this query if a catalog grows large
    enough to need it."""
    with SessionLocal() as session:
        source = session.query(ProductModel).filter_by(
            client_id=client_id, product_type=product_type, work_id=work_id,
        ).first()
        if source is None or source.embedding is None:
            return []

        rows = (
            session.query(
                ProductModel.work_id,
                ProductModel.embedding.cosine_distance(source.embedding).label("distance"),
            )
            .filter(
                ProductModel.client_id == client_id,
                ProductModel.product_type == product_type,
                ProductModel.work_id != work_id,
                ProductModel.embedding.isnot(None),
            )
            .order_by("distance")
            .limit(count)
            .all()
        )
        # Cosine distance is in [0, 2]; 1 - distance gives a similarity in [-1, 1] that
        # behaves like the cosine similarity used elsewhere (higher = more similar).
        return [{"work_id": r.work_id, "semantic_similarity": 1 - r.distance} for r in rows]


def list_model_versions(client_id: int, product_type: str, limit: int = 20) -> list[dict]:
    with SessionLocal() as session:
        rows = (
            session.query(ModelVersionModel)
            .filter_by(client_id=client_id, product_type=product_type)
            .order_by(ModelVersionModel.trained_at.desc())
            .limit(limit)
            .all()
        )
        return [_model_version_row_to_dict(r) for r in rows]
