import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ids import ExternalId

# Public JSON is snake_case throughout (user_id, item_id, recommendation_id, occurred_at,
# viewed_item_ids ...); SDKs convert to their language's idiom.

MAX_BATCH_SIZE = 1000
MAX_PROPERTIES_BYTES = 16 * 1024


def _check_properties_size(value: dict) -> dict:
    """`properties` is deliberately schema-free (price, currency, order_id, brand, any
    tenant-specific field), so the only guard is on total size - it lands in a JSONB column."""
    if len(json.dumps(value, default=str)) > MAX_PROPERTIES_BYTES:
        raise ValueError(f"properties must be at most {MAX_PROPERTIES_BYTES // 1024} KB once serialized")
    return value


class ErrorResponse(BaseModel):
    """Shape of every 4xx/5xx body. `request_id` is also returned in the X-Request-ID
    response header - quote it when reporting a problem."""
    detail: Any = Field(description="Human-readable message (a list of field errors for a 422).")
    request_id: Optional[str] = Field(default=None, examples=["req_01K5Z3W8Q9M2X7T4N6V1B0C8DF"])


# ---------------------------------------------------------------------------
# Items (the catalog)
# ---------------------------------------------------------------------------

class ItemUpsert(BaseModel):
    """Body of PUT /items/{item_id}. Generic on purpose - a product, an article, a listing,
    a job offer, a film: put whatever describes it in `properties`. Only `title` is
    required. `description` (and `properties.category`, when present) feed content-based
    similarity; everything else is stored and returned as-is."""
    title: str = Field(min_length=1, max_length=500, examples=["Nike Air Zoom Pegasus 41"])
    description: Optional[str] = Field(default=None, examples=["Lightweight road-running shoe with responsive cushioning."])
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Free-form attributes. Keys the engine understands when present: `category` "
            "(or `genre`), `author`, `year`, `url`, `price`. Anything else is kept untouched."
        ),
        examples=[{"category": "running-shoes", "price": 129.9, "currency": "EUR", "brand": "Nike", "color": "black"}],
    )
    # Historical top-level fields - still accepted, folded into `properties` (genre_1 ->
    # category). Prefer `properties`.
    genre_1: Optional[str] = Field(default=None, deprecated=True, description="Deprecated: use properties.category.")
    author: Optional[str] = Field(default=None, deprecated=True, description="Deprecated: use properties.author.")
    year: Optional[int] = Field(default=None, deprecated=True, description="Deprecated: use properties.year.")
    url: Optional[str] = Field(default=None, deprecated=True, description="Deprecated: use properties.url.")
    price: Optional[float] = Field(default=None, deprecated=True, description="Deprecated: use properties.price.")

    _properties_size = field_validator("properties")(_check_properties_size)


class Item(BaseModel):
    item_id: ExternalId
    title: str
    description: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "item_id": "SKU-NIKE-001", "title": "Nike Air Zoom Pegasus 41",
        "description": "Lightweight road-running shoe with responsive cushioning.",
        "properties": {"category": "running-shoes", "price": 129.9, "currency": "EUR", "brand": "Nike"},
    }]})


class ItemImportEntry(ItemUpsert):
    item_id: ExternalId


class ItemImportRequest(BaseModel):
    """Batch upsert - each entry behaves exactly like PUT /items/{item_id}."""
    items: List[ItemImportEntry] = Field(min_length=1, max_length=MAX_BATCH_SIZE)

    model_config = ConfigDict(json_schema_extra={"examples": [{"items": [
        {"item_id": "SKU-NIKE-001", "title": "Nike Air Zoom Pegasus 41", "properties": {"category": "running-shoes", "price": 129.9}},
        {"item_id": "SKU-ADIDAS-007", "title": "Adidas Adizero Boston 12", "properties": {"category": "running-shoes", "price": 159.0}},
    ]}]})


class ItemDeleteRequest(BaseModel):
    item_ids: List[ExternalId] = Field(min_length=1, max_length=MAX_BATCH_SIZE, examples=[["SKU-NIKE-001", "SKU-ADIDAS-007"]])


class BatchItemError(BaseModel):
    index: int = Field(description="Position of the failing entry in the request (0-based).")
    id: Optional[str] = Field(default=None, description="Its item_id / user_id, when it had one.")
    message: str


class BatchResult(BaseModel):
    """A bad entry is reported here without failing the others - except when a plan limit is
    reached, which is reported per entry too."""
    received: int
    succeeded: int
    failed: int
    errors: List[BatchItemError] = Field(default_factory=list)

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "received": 3, "succeeded": 2, "failed": 1,
        "errors": [{"index": 2, "id": "SKU-OLD-9", "message": "Free plan limit reached: 50 products max"}],
    }]})


# Item shape of the strategy-specific endpoints' historical array response (RecommendedProduct).
class Product(BaseModel):
    item_id: ExternalId
    # Deprecated integer alias of item_id: present only when the id is a canonical integer.
    work_id: Optional[int] = Field(default=None, deprecated=True)
    title: str
    description: Optional[str] = None
    genre_1: Optional[str] = None
    author: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None
    price: Optional[float] = None
    properties: dict[str, Any] = Field(default_factory=dict)


class SimilarUser(BaseModel):
    user_id: ExternalId
    name: str
    shared_item_ids: List[str] = Field(default_factory=list)
    shared_work_ids: List[int] = Field(default_factory=list, deprecated=True)


class Explanation(BaseModel):
    reason: str
    content_similarity: Optional[float] = None
    semantic_similarity: Optional[float] = None
    popularity_score: Optional[float] = None
    # Kept for existing consumers - purchase-specific, always computed from "purchase"-
    # typed events only. interaction_count below is the generic equivalent that works
    # for any vertical (reservations, watch events, ...), and is what "reason" is now
    # built from - see compute_popularity_scores in modelData.py.
    purchase_count: Optional[int] = None
    interaction_count: Optional[int] = None
    interaction_label: Optional[str] = None
    collaborative_score: Optional[float] = None
    similar_users: Optional[List[SimilarUser]] = None
    source_item_ids: Optional[List[str]] = None
    source_work_ids: Optional[List[int]] = Field(default=None, deprecated=True)


class RecommendedProduct(Product):
    score: Optional[float] = None
    explanation: Optional[Explanation] = None


class VectorRecommendation(BaseModel):
    item_id: Optional[str] = None
    work_id: str = Field(deprecated=True, description="Deprecated alias of item_id.")
    title: str


# --- The common recommendation response --------------------------------------------------

class RecommendationStrategy(str, Enum):
    """The strategy that actually produced the items (after any fallback)."""
    hybrid = "hybrid"
    content = "content"
    collaborative = "collaborative"
    session = "session"
    popular = "popular"


class SimilarUserPublic(BaseModel):
    user_id: str
    shared_item_ids: List[str] = Field(default_factory=list)


class RecommendationExplanation(BaseModel):
    """Why an item was recommended. `reason` is always present and human-readable; the
    numeric signals are present when the strategy computed them. `similar_users` only appears
    when the request asked for `debug` with the secret key (it points at other users)."""
    reason: str
    content_similarity: Optional[float] = None
    semantic_similarity: Optional[float] = None
    popularity_score: Optional[float] = None
    interaction_count: Optional[int] = None
    interaction_label: Optional[str] = None
    collaborative_score: Optional[float] = None
    source_item_ids: Optional[List[str]] = None
    similar_users: Optional[List[SimilarUserPublic]] = None


class RecommendedItem(BaseModel):
    item_id: ExternalId
    score: Optional[float] = Field(default=None, description="Higher is better; comparable within one response only.")
    title: Optional[str] = None
    description: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)
    explanation: Optional[RecommendationExplanation] = None


class RecommendationResponse(BaseModel):
    """`recommendation_id` and `items` are always present. Send `recommendation_id` back on
    the impression / click / add_to_cart / purchase events for these items to attribute them
    to this call."""
    recommendation_id: str = Field(examples=["rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF"])
    strategy: RecommendationStrategy
    placement: Optional[str] = Field(default=None, examples=["homepage"])
    items: List[RecommendedItem]

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "recommendation_id": "rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF", "strategy": "hybrid", "placement": "product_page",
        "items": [{
            "item_id": "SKU-ADIDAS-007", "score": 0.934, "title": "Adidas Adizero Boston 12",
            "description": "Fast road-running shoe with a carbon plate.",
            "properties": {"category": "running-shoes", "price": 159.0},
            "explanation": {"reason": "Hybride : 50% collaboratif + 50% contenu (similaire à « Nike Air Zoom Pegasus 41 »)", "content_similarity": 0.91, "collaborative_score": 0.96},
        }],
    }]})


class RecommendationRequest(BaseModel):
    """Everything is optional except `count`: send what you know and LIKYLY picks the best
    strategy (user+item -> hybrid, item -> content, viewed items / session -> session,
    user -> collaborative, nothing -> popular), falling back gracefully when a signal has no
    data behind it."""
    user_id: Optional[ExternalId] = Field(default=None, description="Identified user.", examples=["user_123"])
    session_id: Optional[str] = Field(default=None, max_length=255, description="Anonymous visitor / browsing session.", examples=["sess_abc"])
    item_id: Optional[ExternalId] = Field(default=None, description="The item being looked at (e.g. the product page).", examples=["item_456"])
    viewed_item_ids: List[ExternalId] = Field(default_factory=list, max_length=50, description="Recently viewed items, oldest first.")
    placement: Optional[str] = Field(default=None, max_length=128, description="Free-form label of where the recommendations will be shown.", examples=["homepage"])
    count: int = Field(default=10, ge=1, le=100)
    debug: bool = Field(default=False, description="Include diagnostic detail in explanations. Secret key only.")

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"user_id": "user_123", "session_id": "sess_abc", "item_id": "item_456", "placement": "product_page", "count": 10},
        {"session_id": "sess_123", "viewed_item_ids": ["item_123", "item_456"], "count": 10},
        {"count": 10},
    ]})


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class UserUpsert(BaseModel):
    """Body of PUT /users/{user_id} - a schema-free profile. Users are optional enrichment:
    events with a user_id work without one ever being created."""
    properties: dict[str, Any] = Field(
        default_factory=dict,
        examples=[{"country": "FR", "age": 34, "segment": "premium"}],
    )
    user_gender: Optional[str] = Field(default=None, deprecated=True, description="Deprecated: use properties.")
    user_age: Optional[int] = Field(default=None, deprecated=True, description="Deprecated: use properties.")
    user_zip: Optional[int] = Field(default=None, deprecated=True, description="Deprecated: use properties.")
    user_firstname: Optional[str] = Field(default=None, deprecated=True, description="Deprecated: use properties.firstname.")
    user_lastname: Optional[str] = Field(default=None, deprecated=True, description="Deprecated: use properties.lastname.")

    _properties_size = field_validator("properties")(_check_properties_size)


class User(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "user_id": "user_123", "properties": {"country": "FR", "age": 34, "segment": "premium"},
    }]})

    user_id: ExternalId
    properties: dict[str, Any] = Field(default_factory=dict)
    user_gender: Optional[str] = Field(default=None, deprecated=True)
    user_age: Optional[int] = Field(default=None, deprecated=True)
    user_zip: Optional[int] = Field(default=None, deprecated=True)
    user_firstname: Optional[str] = Field(default=None, deprecated=True)
    user_lastname: Optional[str] = Field(default=None, deprecated=True)
    user_firstlastname: Optional[str] = Field(default=None, deprecated=True)


class UserImportEntry(UserUpsert):
    user_id: ExternalId


class UserImportRequest(BaseModel):
    users: List[UserImportEntry] = Field(min_length=1, max_length=MAX_BATCH_SIZE)

    model_config = ConfigDict(json_schema_extra={"examples": [{"users": [
        {"user_id": "user_123", "properties": {"country": "FR", "segment": "premium"}},
        {"user_id": "user_456", "properties": {"country": "DE"}},
    ]}]})


class Message(BaseModel):
    message: str


class GenerateModelJobStatus(BaseModel):
    job_id: str
    status: str
    data_product_type: str
    detail: Optional[str] = None
    version_id: Optional[int] = None
    precision_at_k: Optional[float] = None
    promoted: Optional[bool] = None


class ModelVersion(BaseModel):
    id: int
    trained_at: datetime
    factors: int
    regularization: float
    iterations: int
    precision_at_k: Optional[float] = None
    num_users: Optional[int] = None
    num_items: Optional[int] = None
    num_interactions: Optional[int] = None
    # (the artifact's file_path is a server-side path: never part of the public contract)
    is_active: bool
    # "manual" (client called /generateModel) or "auto" (auto-retrain, triggered by
    # accumulated interaction volume) - lets the client dashboard show which kind of
    # training last updated their model.
    triggered_by: str


class ModelStatus(BaseModel):
    """Everything a client dashboard needs to show "your model" in one call: which
    version is live and how it got there, plus today's quota usage - so a free-tier
    client can see clearly why a training run was refused or an auto-retrain skipped,
    rather than just noticing nothing changed."""
    data_product_type: str
    active_version: Optional[ModelVersion] = None
    manual_trainings_today: int
    # None = unlimited (the demo client) - a real paid-plan limit isn't modeled yet.
    manual_training_daily_limit: Optional[int] = None
    auto_retrains_today: int
    auto_retrain_daily_limit: Optional[int] = None
    auto_retrain_skipped_today: bool = False
    auto_retrain_skip_reason: Optional[str] = None


class Event(BaseModel):
    """Body of POST /events/{event_type} (and of /events/view, /events/purchase). The event
    type is in the URL; this is the event itself. Works for identified users, anonymous
    visitors, or both at once (which is what later lets an anonymous history be attached to
    the user who logs in)."""
    item_id: Optional[ExternalId] = Field(default=None, description="The item the interaction is about. Required.", examples=["item_456"])
    user_id: Optional[ExternalId] = Field(default=None, description="Identified user. At least one of user_id / session_id is required.", examples=["user_123"])
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=255, description="Anonymous visitor / browsing session.", examples=["sess_abc"])
    recommendation_id: Optional[str] = Field(default=None, max_length=128, description="The recommendation_id of the recommendation call that surfaced this item - enables attribution.", examples=["rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF"])
    placement: Optional[str] = Field(default=None, max_length=128, description="Free-form label of where this happened.", examples=["homepage"])
    quantity: int = Field(default=1, ge=0, le=1_000_000, description="Magnitude in whatever unit fits the event type (units bought, seconds watched, ...). Scales the event's weight in training.")
    occurred_at: Optional[datetime] = Field(default=None, description="When it happened (ISO 8601). Defaults to the server's time; a time without a zone is read as UTC.", examples=["2026-09-24T10:30:00Z"])
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSON stored with the event and never interpreted: price, currency, order_id, revenue, any tenant-specific field.",
        examples=[{"price": 129.90, "currency": "EUR", "order_id": "ORD-123"}],
    )
    event_id: Optional[str] = Field(default=None, min_length=1, max_length=255, description="Optional idempotency key, unique per account: replaying an event with an already-seen event_id records nothing and returns `duplicate: true`. Recommended on purchases.", examples=["evt_customer_1234"])
    work_id: Optional[ExternalId] = Field(default=None, deprecated=True, description="Deprecated alias of item_id.")

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "user_id": "user_123", "session_id": "sess_abc", "item_id": "item_456",
        "recommendation_id": "rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF", "placement": "homepage", "quantity": 1,
        "occurred_at": "2026-09-24T10:30:00Z",
        "properties": {"price": 129.90, "currency": "EUR", "order_id": "ORD-123"},
        "event_id": "evt_customer_1234",
    }]})

    _properties_size = field_validator("properties")(_check_properties_size)

    @model_validator(mode="before")
    @classmethod
    def _accept_work_id_alias(cls, data: Any) -> Any:
        # Legacy `work_id` becomes item_id. Done on the raw dict (not in an after-validator)
        # because reading a deprecated field off the instance emits a DeprecationWarning.
        if isinstance(data, dict) and data.get("work_id") is not None:
            data = dict(data)
            if data.get("item_id") is None:
                data["item_id"] = data["work_id"]
            elif str(data["item_id"]) != str(data["work_id"]):
                raise ValueError("item_id and work_id (deprecated alias) disagree - send item_id only")
        return data

    @model_validator(mode="after")
    def _require_item_and_actor(self) -> "Event":
        if self.item_id is None:
            raise ValueError("item_id is required")
        if self.user_id is None and self.session_id is None:
            raise ValueError("at least one of user_id or session_id is required")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=timezone.utc)
        return self


class EventBatchItem(Event):
    event_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$", description="e.g. view, click, add_to_cart, purchase - or any type of your own.", examples=["click"])


class EventBatch(BaseModel):
    """Up to 1000 events in one request. Validated as a whole (one malformed event -> 422
    naming its position, nothing recorded); then recorded together, with event_id-based
    de-duplication applied per event."""
    events: List[EventBatchItem] = Field(min_length=1, max_length=MAX_BATCH_SIZE)

    model_config = ConfigDict(json_schema_extra={"examples": [{"events": [
        {"event_type": "impression", "session_id": "sess_abc", "item_id": "item_456", "recommendation_id": "rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF", "placement": "homepage"},
        {"event_type": "click", "session_id": "sess_abc", "item_id": "item_456", "recommendation_id": "rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF", "placement": "homepage"},
    ]}]})


class EventResult(BaseModel):
    message: str
    event_id: Optional[str] = None
    duplicate: bool = Field(default=False, description="True if this event_id was already recorded - nothing was written.")

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"message": "purchase event recorded", "event_id": "evt_customer_1234", "duplicate": False},
        {"message": "purchase event already recorded (duplicate event_id ignored)", "event_id": "evt_customer_1234", "duplicate": True},
    ]})


class EventBatchResult(BaseModel):
    received: int
    accepted: int
    duplicates: int = Field(description="Events skipped because their event_id was already recorded.")

    model_config = ConfigDict(json_schema_extra={"examples": [{"received": 3, "accepted": 2, "duplicates": 1}]})


class ClientSelf(BaseModel):
    client_id: int
    name: str
    # Only ever present right after creation or a regeneration of that specific key -
    # raw values are never stored, so they can't be shown again on a later call.
    secret_key: Optional[str] = None
    public_key: Optional[str] = None
    has_public_key: bool = True
    # Purely informational, to power an "X days old, rotation conseillée" hint in the
    # account UI - rotation stays manual (regenerate-*-key), nothing reads these to
    # enforce anything server-side.
    secret_key_rotated_at: Optional[datetime] = None
    public_key_rotated_at: Optional[datetime] = None
    # Which catalog namespace(s) this client has pushed products for - lets the account
    # dashboard know which product_type(s) to fetch quota/model status for, without an
    # extra round trip (this endpoint is already called on every /account page load).
    product_types: List[str] = []


class ClientUsageSummary(BaseModel):
    """Account-wide (not per-product_type) quota numbers for the self-service dashboard -
    separate from ModelStatus, which is inherently scoped to one product_type."""
    plan: str
    product_count: int
    product_limit: Optional[int] = None


class ClientAdminView(BaseModel):
    id: int
    name: str
    # Sourced from the Supabase session JWT at each self-service login (see
    # get_or_create_my_client) - null for manually-provisioned clients, which have no
    # linked Supabase account.
    contact_email: Optional[str] = None
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    is_self_service: bool
    has_secret_key: bool
    secret_key_rotated_at: Optional[datetime] = None
    has_public_key: bool
    public_key_rotated_at: Optional[datetime] = None
    total_requests: int
    plan: str


class DailyUsage(BaseModel):
    date: datetime
    request_count: int


class ClientRename(BaseModel):
    name: Optional[str] = None
    # "free" or "unlimited" today - see VALID_PLANS in db.py. Optional so a plain rename
    # doesn't need to resend the current plan.
    plan: Optional[str] = None


class EventType(BaseModel):
    event_type: str
    label: str
    # "faible"/"moyen"/"fort" - see EVENT_TIER_WEIGHTS in db.py. The tenant only ever
    # picks a tier, never a raw weight - weight is included here purely for transparency
    # (e.g. a technical integrator inspecting the API), not meant to be edited directly.
    tier: str
    weight: float
    created_at: datetime


class EventTypeCreate(BaseModel):
    event_type: str
    label: str
    tier: str


class EventTypeUpdate(BaseModel):
    label: Optional[str] = None
    tier: Optional[str] = None


class ImportRowError(BaseModel):
    row: int
    message: str


class ImportSummary(BaseModel):
    """Returned by every /clients/me/import/* endpoint - a bad row (missing required
    column, unparseable value) is skipped and reported, not fatal to the whole import,
    since a non-technical tenant's first CSV export will rarely be perfectly clean."""
    rows_total: int
    rows_ok: int
    errors: List[ImportRowError]
