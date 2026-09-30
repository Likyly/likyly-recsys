import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Literal, Optional

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


def _single_line(value: str) -> str:
    """Collapses any whitespace run (newlines and control characters included) to one space - these
    values end up in an email subject/headers, where a newline would be header injection."""
    return " ".join(value.split())


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


class IdentifyRequest(BaseModel):
    """Body of POST /events/identify - call it right after login to attach an anonymous
    session's *past* interactions to the now-known user, so they count for that user's
    collaborative/session history instead of being stranded under a session_id no one will
    query again. Only interactions not already attributed to a user are touched; a user_id
    unknown to LIKYLY yet is fine (created the same way any other first-seen id is)."""
    user_id: ExternalId
    session_id: str = Field(min_length=1, max_length=255)


class IdentifyResult(BaseModel):
    linked_interactions: int = Field(description="How many of this session's past interactions now belong to user_id.")


class ClientSelf(BaseModel):
    client_id: int
    # Technical name ("li_" + code): fixed, the identifier to give support.
    name: str
    # The name the owner chose in the dashboard, null until they set one.
    display_name: Optional[str] = None
    # Non-secret previews ("sk_yVzt…gjP0") of the current keys - what the console shows once a key can no
    # longer be revealed. Null for a key issued before previews existed (regenerate it to get one).
    secret_key_hint: Optional[str] = None
    public_key_hint: Optional[str] = None
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


class WorkspaceSummary(BaseModel):
    """One workspace this account owns - GET /clients/me/workspaces, the switcher list.
    No keys here (those are only ever shown by /clients/me and the regenerate-*-key routes,
    scoped to whichever workspace X-Workspace-Id selects)."""
    id: int
    name: str
    display_name: Optional[str] = None
    plan: str
    is_active: bool
    created_at: datetime


class WorkspaceRename(BaseModel):
    display_name: str = Field(min_length=1, max_length=200, description="The name shown for the workspace (whitespace around it is trimmed). The technical `name` never changes.", examples=["Acme Store"])

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = _single_line(value)
        if not value:
            raise ValueError("Workspace name cannot be blank")
        return value


class CatalogIndex(BaseModel):
    """One index of the workspace = one catalog namespace (product_type): its own items, its
    own trained model. Indexes have no separate lifecycle - one appears as soon as a data
    source or an item push writes into a new product_type."""
    name: str = Field(description="The index name - the product_type / data_product_type used everywhere else in this API.")
    product_count: int
    data_source_count: int


class ClientUsageSummary(BaseModel):
    """Account-wide (not per-product_type) quota numbers for the self-service dashboard -
    separate from ModelStatus, which is inherently scoped to one product_type. Backs the
    workspace view: how many catalogs/products this account has connected/pushed, and how
    many its plan allows - free is 1 catalog / 1 data source / 50 products, Pro raises all three."""
    plan: str
    product_count: int
    product_limit: Optional[int] = None
    index_count: int
    index_limit: Optional[int] = None
    data_source_count: int
    data_source_limit: Optional[int] = None


# --- Plan upgrade request ----------------------------------------------------------------
# Answers are closed lists (ranges, stages) rather than free text wherever possible: easier to
# fill in, and comparable across requests. The human labels used in the notification email live
# in upgrade_requests.py, next to the code that renders it.

UpgradeSector = Literal["ecommerce", "media", "saas", "marketplace", "culture_leisure", "travel", "other"]
UpgradeCatalogSize = Literal["lt_1k", "1k_10k", "10k_100k", "100k_1m", "gt_1m"]
UpgradeIndexesNeeded = Literal["1", "2_5", "6_10", "gt_10"]
UpgradeMonthlyVisitors = Literal["lt_10k", "10k_100k", "100k_1m", "gt_1m", "unknown"]
UpgradeSyncFrequency = Literal["daily", "hourly", "near_realtime", "unsure"]
UpgradeMaturity = Literal["prototype", "preproduction", "production_early", "production_established"]
UpgradeGoLive = Literal["asap", "lt_1m", "1_3m", "gt_3m", "exploring"]
UpgradeCurrentSolution = Literal["none", "in_house", "third_party"]


class UpgradeRequestCreate(BaseModel):
    """The plan upgrade form. There is no online payment yet: submitting this records the request
    and emails the team, who get back to the account's email address. Only who is asking is
    required (company, sector, name, consent); everything about the site and the need is optional -
    a blank answer is stored as null and shown as "not provided" in the email."""
    company_name: str = Field(min_length=1, max_length=200, examples=["Acme Store"])
    activity_sector: UpgradeSector
    contact_first_name: str = Field(min_length=1, max_length=100)
    contact_last_name: str = Field(min_length=1, max_length=100)
    consent: bool = Field(description="Must be true: agrees to be contacted about this request.")

    contact_role: Optional[str] = Field(default=None, max_length=200)
    website_url: Optional[str] = Field(default=None, max_length=500, description="The site or app this would run on. `https://` is added when omitted.", examples=["https://acme-store.example"])
    activity_description: Optional[str] = Field(default=None, max_length=1000, description="What the company does, in a few words.")
    catalog_size: Optional[UpgradeCatalogSize] = Field(default=None, description="Items the catalog(s) would hold.")
    indexes_needed: Optional[UpgradeIndexesNeeded] = Field(default=None, description="Separate catalogs wanted.")
    monthly_visitors: Optional[UpgradeMonthlyVisitors] = Field(default=None, description="Monthly visitors of the site or app.")
    sync_frequency: Optional[UpgradeSyncFrequency] = Field(default=None, description="How often the catalog should be refreshed.")
    maturity: Optional[UpgradeMaturity] = Field(default=None, description="Where the site or app is at.")
    go_live: Optional[UpgradeGoLive] = Field(default=None, description="When recommendations should be live.")
    current_solution: Optional[UpgradeCurrentSolution] = Field(default=None, description="What recommends products today.")
    message: Optional[str] = Field(default=None, max_length=2000, description="Anything else worth knowing.")

    @field_validator("catalog_size", "indexes_needed", "monthly_visitors", "sync_frequency", "maturity", "go_live", "current_solution", mode="before")
    @classmethod
    def _blank_choice_is_no_answer(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("company_name", "contact_first_name", "contact_last_name", "contact_role")
    @classmethod
    def _one_line(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = _single_line(value)
        return value or None

    @field_validator("company_name", "contact_first_name", "contact_last_name")
    @classmethod
    def _not_blank(cls, value: Optional[str]) -> str:
        if not value:
            raise ValueError("Cannot be blank")
        return value

    @field_validator("activity_description", "message")
    @classmethod
    def _multi_line(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = "".join(ch for ch in value if ch in "\n\t" or ch.isprintable()).strip()
        return cleaned or None

    @field_validator("website_url")
    @classmethod
    def _valid_url(cls, value: Optional[str]) -> Optional[str]:
        from urllib.parse import urlparse
        if value is None or not value.strip():
            return None
        value = value.strip()
        if "://" not in value:
            value = f"https://{value}"
        parts = urlparse(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or any(ch.isspace() for ch in value) or "." not in parts.netloc:
            raise ValueError("Must be a valid website address, e.g. https://example.com")
        return value

    @field_validator("consent")
    @classmethod
    def _must_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Consent is required to be contacted about this request")
        return value


class UpgradeRequestReceived(BaseModel):
    id: int
    created_at: datetime


class PlanLimits(BaseModel):
    """What one plan allows. `null` = no cap."""
    plan: str
    index_limit: Optional[int] = None
    data_source_limit: Optional[int] = None
    product_limit: Optional[int] = None
    manual_sync_daily_limit: Optional[int] = Field(default=None, description="Catalog synchronizations per day, per data source.")
    manual_training_daily_limit: Optional[int] = Field(default=None, description="Manual model trainings per day, per index.")
    auto_retrain_daily_limit: Optional[int] = None
    workspace_limit: Optional[int] = Field(default=None, description="Workspaces this account may own.")
    analytics_window_days: Optional[int] = Field(default=None, description="Longest performance-dashboard window this plan can request.")
    analytics_detail: bool = Field(default=False, description="Whether the trend/by-placement/revenue breakdown is available (GET .../analytics/detail).")


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


# ---------------------------------------------------------------------------
# Developer keys - a third credential kind for tenant-scoped tooling (Claude Code, Codex),
# distinct from the secret/public keys - see ALLOWED_SCOPES in db.py.
# ---------------------------------------------------------------------------

class DeveloperKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, examples=["Claude Code - data sources"])
    scopes: List[str] = Field(
        min_length=1,
        description="e.g. sources:read, sources:write - see GET /data-sources/types' sibling docs for the full vocabulary. Unknown scopes are silently dropped.",
        examples=[["sources:read", "sources:write"]],
    )


class DeveloperKey(BaseModel):
    id: int
    name: str
    key_hint: Optional[str] = Field(default=None, description="Non-secret preview, e.g. `lk_Ab3x…9QpL`. Null for keys issued before previews existed.")
    scopes: List[str]
    created_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class DeveloperKeyCreated(DeveloperKey):
    key: str = Field(description="Shown once - store it now, it can't be retrieved again.")


# ---------------------------------------------------------------------------
# Data sources - see application/utils/connectors and application/utils/sync_engine.
# ---------------------------------------------------------------------------

class SourceTypeOut(BaseModel):
    id: str
    label: str
    description: str
    supports_incremental: bool
    requires_credentials: bool
    fixed_sync_mode: Optional[str] = None


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, examples=["Shopify catalog"])
    type: str = Field(description="One of GET /data-sources/types' ids, e.g. 'shopify', 'rest_api', 'csv_url'.", examples=["shopify"])
    product_type: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$', description="The catalog namespace this source writes into (data_product_type elsewhere in this API).")
    config: dict[str, Any] = Field(default_factory=dict, description="Non-secret settings - shape depends on `type` (see docs/data-sources.md).")
    credentials: Optional[dict[str, Any]] = Field(default=None, description="Secrets (access tokens, API keys) - encrypted at rest, never returned by any endpoint.")
    field_mapping: Optional[dict[str, Any]] = Field(default=None, description="Can be set later via PUT .../field-mapping instead.")
    sync_mode: Optional[str] = Field(default=None, description="'full' or 'incremental' - defaults to 'incremental' if the type supports it, else 'full'. Ignored (forced) for push-mode types (woocommerce, webhook).")
    schedule: Optional[str] = Field(default=None, description="Reserved for a future scheduler - manual sync only for now regardless of this value.")


class DataSourceUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    credentials: Optional[dict[str, Any]] = None
    sync_mode: Optional[str] = None
    schedule: Optional[str] = None


class DataSource(BaseModel):
    id: int
    name: str
    type: str
    product_type: str
    config: dict[str, Any]
    has_credentials: bool
    field_mapping: Optional[dict[str, Any]] = None
    sync_mode: str
    schedule: Optional[str] = None
    status: str
    last_sync_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DataSourceCreated(DataSource):
    push_secret: Optional[str] = Field(default=None, description="Only present for push-mode types (woocommerce, webhook) - shown once, configure the upstream system with it.")


class ConnectionTestResultOut(BaseModel):
    ok: bool
    message: str
    detail: Optional[dict[str, Any]] = None


class PreviewResultOut(BaseModel):
    sample: List[dict[str, Any]]
    detected_fields: List[str]
    suggested_mapping: dict[str, Any]


class FieldMappingIn(BaseModel):
    mapping: dict[str, Any] = Field(
        description=(
            "external_id/title required; description, category, price, image, url, stock, "
            "updated_at optional; attributes is itself {name: path}. Each value is a field name "
            "or a dotted/bracket path (e.g. 'variants[0].price')."
        ),
        examples=[{"external_id": "id", "title": "name", "price": "variants[0].price", "image": "images[0].src"}],
    )


class FieldMappingDryRunOut(BaseModel):
    normalized_sample: List[dict[str, Any]]
    errors: List[str]


class SyncTriggerRequest(BaseModel):
    mode: str = Field(default="incremental", description="'full' or 'incremental'. Falls back to 'full' if the source doesn't support incremental.")


class SyncRun(BaseModel):
    id: int
    data_source_id: int
    mode: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    items_fetched: int
    items_upserted: int
    items_deleted: int
    items_failed: int
    error_summary: Optional[str] = None


class CatalogStats(BaseModel):
    data_source_id: int
    product_type: str
    item_count: int
    last_item_write_at: Optional[datetime] = None
    last_sync: Optional[SyncRun] = None


class PushSecretIssued(BaseModel):
    push_secret: str = Field(description="Shown once - configure the upstream system's webhook with it, it can't be retrieved again.")


class PushPayload(BaseModel):
    """Body of POST /data-sources/{id}/push - the webhook/generic push ingress."""
    items: List[dict[str, Any]] = Field(default_factory=list, max_length=MAX_BATCH_SIZE)
    deleted_ids: List[str] = Field(default_factory=list, max_length=MAX_BATCH_SIZE)


# ---------------------------------------------------------------------------
# Placements - an orchestration layer over the recommendation strategies above (content,
# session, collaborative, hybrid, popular, or "auto" - recommend_auto's own signal-based
# selection). See application/api/placement_engine.py and docs/placements.md.
# ---------------------------------------------------------------------------

PlacementStrategy = Literal["auto", "content", "session", "collaborative", "hybrid", "popular"]
PlacementFallbackStrategy = Literal["content", "session", "collaborative", "hybrid", "popular", "auto"]
PlacementContextType = Literal[
    "product_page", "listing_page", "category_page", "homepage", "cart", "account",
    "content_page", "custom",
]
PlacementAudience = Literal["all", "anonymous", "identified"]
PlacementSignalName = Literal[
    "current_item_id", "category_id", "collection_id", "user_id", "anonymous_id",
    "session_id", "cart_item_ids", "locale", "custom_context",
]


class PlacementSignals(BaseModel):
    """Which context keys this placement consumes. Every context key is optional to send
    unless it's listed in `required` - a /recommend or /preview call missing one is rejected
    (422) / reported in `errors` before any strategy runs."""
    required: List[PlacementSignalName] = Field(default_factory=list)
    optional: List[PlacementSignalName] = Field(default_factory=list)


class PlacementAttributeRule(BaseModel):
    attribute: str = Field(description="An item properties key, e.g. 'brand', 'category'.")
    values: List[Any] = Field(min_length=1)


class PlacementFilters(BaseModel):
    """Attribute-based inclusion rules - see application/api/placement_filters.py. A
    category_id in the request context implicitly scopes results to it when category_in
    isn't set here."""
    category_in: Optional[List[str]] = None
    category_not_in: Optional[List[str]] = None
    in_stock_only: bool = Field(default=False, description="Only filters items that actually have a `stock` or `in_stock` property - never excludes an item for lacking one.")
    include_attributes: List[PlacementAttributeRule] = Field(default_factory=list)
    exclude_attributes: List[PlacementAttributeRule] = Field(default_factory=list)


class PlacementBusinessRules(BaseModel):
    exclude_current_item: bool = Field(default=True, description="Never recommend the item named in context.current_item_id back to itself.")
    exclude_cart_items: bool = Field(default=False, description="Never recommend an item already in context.cart_item_ids.")


class PlacementCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$', examples=["pdp-related"])
    name: str = Field(min_length=1, max_length=200, examples=["Related products (PDP)"])
    context_type: PlacementContextType = "custom"
    product_type: Optional[str] = Field(default=None, min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$', description="Catalog namespace to recommend from - omit it when this account has a single catalog.")
    limit: int = Field(default=10, ge=1, le=100)
    audience: PlacementAudience = "all"
    strategy: PlacementStrategy = "auto"
    signals: PlacementSignals = Field(default_factory=PlacementSignals)
    fallback_strategy: Optional[PlacementFallbackStrategy] = Field(default=None, description="Tried if `strategy` returns no results. 'popular' is always the final safety net regardless of this.")
    filters: PlacementFilters = Field(default_factory=PlacementFilters)
    business_rules: PlacementBusinessRules = Field(default_factory=PlacementBusinessRules)
    tracking_configuration: dict[str, Any] = Field(default_factory=dict, description="Reserved for a future placement-level tracking override.")
    enabled: bool = True

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "slug": "pdp-related", "name": "Related products (PDP)", "context_type": "product_page",
        "limit": 4, "strategy": "auto", "signals": {"required": ["current_item_id"], "optional": ["session_id", "user_id"]},
        "business_rules": {"exclude_current_item": True},
    }]})


class PlacementUpdate(BaseModel):
    name: Optional[str] = None
    context_type: Optional[PlacementContextType] = None
    product_type: Optional[str] = Field(default=None, min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    limit: Optional[int] = Field(default=None, ge=1, le=100)
    audience: Optional[PlacementAudience] = None
    strategy: Optional[PlacementStrategy] = None
    signals: Optional[PlacementSignals] = None
    fallback_strategy: Optional[PlacementFallbackStrategy] = None
    filters: Optional[PlacementFilters] = None
    business_rules: Optional[PlacementBusinessRules] = None
    tracking_configuration: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None


class Placement(BaseModel):
    slug: str
    name: str
    context_type: PlacementContextType
    product_type: Optional[str] = None
    limit: int
    audience: PlacementAudience
    strategy: PlacementStrategy
    signals: PlacementSignals
    fallback_strategy: Optional[PlacementFallbackStrategy] = None
    filters: PlacementFilters
    business_rules: PlacementBusinessRules
    tracking_configuration: dict[str, Any]
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime


class PlacementContext(BaseModel):
    """Every field optional unless the placement's own `signals.required` names it - see
    GET /placements/{slug}/requirements."""
    current_item_id: Optional[ExternalId] = Field(default=None, description="The product/article/listing currently being viewed.")
    category_id: Optional[str] = None
    collection_id: Optional[str] = None
    user_id: Optional[ExternalId] = Field(default=None, description="Identified visitor.")
    anonymous_id: Optional[str] = Field(default=None, description="Stable anonymous visitor id - used like session_id when session_id is omitted.")
    session_id: Optional[str] = Field(default=None, max_length=255, description="Anonymous browsing session.")
    cart_item_ids: List[ExternalId] = Field(default_factory=list, max_length=200)
    locale: Optional[str] = None
    custom_context: dict[str, Any] = Field(default_factory=dict)


class PlacementRecommendRequest(BaseModel):
    context: PlacementContext = Field(default_factory=PlacementContext)
    limit: Optional[int] = Field(default=None, ge=1, le=100, description="Overrides the placement's own `limit` for this call.")

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"context": {"current_item_id": "SKU-NIKE-001", "session_id": "sess_abc"}, "limit": 4},
    ]})


class PlacementRecommendResponse(BaseModel):
    recommendation_id: str = Field(examples=["rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF"])
    placement: str = Field(examples=["pdp-related"])
    strategy_used: RecommendationStrategy
    items: List[RecommendedItem]


class PlacementPreviewRequest(BaseModel):
    context: PlacementContext = Field(default_factory=PlacementContext)
    limit: Optional[int] = Field(default=None, ge=1, le=100)


class PlacementPreviewOut(BaseModel):
    """Never writes anything (no recommendation_id minted) and never includes other users'
    data - present_records' own include_similar_users=False already keeps that out."""
    strategy_used: Optional[RecommendationStrategy] = None
    items: List[RecommendedItem] = Field(default_factory=list)
    fallback_used: bool = False
    attempted: List[str] = Field(default_factory=list)
    signals_used: dict[str, bool] = Field(default_factory=dict, description="Which context keys were actually present in the request.")
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list, description="Non-empty only when a required signal was missing - strategy_used/items are then absent rather than wrong.")


class PlacementRequirementsOut(BaseModel):
    """Static introspection - no engine call, no context needed."""
    slug: str
    context_type: PlacementContextType
    strategy: PlacementStrategy
    fallback_strategy: Optional[PlacementFallbackStrategy] = None
    audience: PlacementAudience
    signals: PlacementSignals
    enabled: bool


# ---------------------------------------------------------------------------
# Integration validator - get_placement_health / get_tracking_requirements /
# get_recent_integration_events (MCP), backed by application/api/placement_service.py.
# ---------------------------------------------------------------------------

HealthStatus = Literal["ok", "warning", "missing"]


class PlacementHealthCheck(BaseModel):
    name: str = Field(examples=["purchases_received"])
    status: HealthStatus
    count: int
    message: str


class PlacementHealthOut(BaseModel):
    """The brief's checklist (✓ recommendations requested / ⚠ add_to_cart not received / ✗
    purchases not received, ...), as structured data - an MCP tool renders the symbols, this
    endpoint just reports facts, counted over the last `window_hours`."""
    slug: str
    window_hours: int
    overall: HealthStatus
    checks: List[PlacementHealthCheck]


class RequiredEvent(BaseModel):
    event_type: str = Field(examples=["recommendation_impression"])
    scope: Literal["placement", "catalog"] = Field(description="'placement': tagged with this placement's slug. 'catalog': store-wide, not placement-specific.")
    why: str


class TrackingRequirementsOut(BaseModel):
    """Static (no query) - which events this placement needs instrumented, and why. What
    `get_integration_recipe`'s `requiredEvents` and the `get_tracking_requirements` MCP tool
    are built from."""
    slug: str
    required_context: PlacementSignals
    required_events: List[RequiredEvent]


class RecentRecommendationCall(BaseModel):
    recommendation_id: str
    strategy: str
    item_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    created_at: datetime


class RecentInteraction(BaseModel):
    event_type: str
    item_id: Optional[str] = None
    session_id: Optional[str] = None
    occurred_at: datetime


class RecentIntegrationEventsOut(BaseModel):
    slug: str
    window_hours: int
    recommendation_calls: List[RecentRecommendationCall]
    interactions: List[RecentInteraction]


# ---------------------------------------------------------------------------
# Performance dashboard - GET /clients/me/analytics/*
# ---------------------------------------------------------------------------


class AnalyticsSummaryOut(BaseModel):
    """Always available on any plan - the Free tier's whole view. `start`/`end` reflect the
    window the server actually used (clamped to the caller's plan), not necessarily what was
    asked for."""
    start: str
    end: str
    recommendations_served: int
    impressions: int
    clicks: int
    ctr: float


class AnalyticsTimeseriesPoint(BaseModel):
    date: str
    recommendations_served: int
    impressions: int
    clicks: int
    ctr: float


class AnalyticsPlacementRow(BaseModel):
    placement: str
    recommendations_served: int
    impressions: int
    clicks: int
    ctr: float


class AnalyticsRevenue(BaseModel):
    purchases: int
    revenue: float


class AnalyticsDetailOut(BaseModel):
    """Pro-only - the depth Free's summary doesn't include."""
    start: str
    end: str
    timeseries: List[AnalyticsTimeseriesPoint]
    by_placement: List[AnalyticsPlacementRow]
    revenue: AnalyticsRevenue
