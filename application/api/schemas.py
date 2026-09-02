from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class Product(BaseModel):
    work_id: int
    title: str
    # Only work_id/title are guaranteed: a lightweight content profile pushed via
    # PUT /products/{id}/profile may not set every field (price/url/etc. often stay in
    # the caller's own system entirely).
    description: Optional[str] = None
    genre_1: Optional[str] = None
    author: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None
    price: Optional[float] = None


class SimilarUser(BaseModel):
    user_id: int
    name: str
    shared_work_ids: List[int]


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
    source_work_ids: Optional[List[int]] = None


class RecommendedProduct(Product):
    score: Optional[float] = None
    explanation: Optional[Explanation] = None


class VectorRecommendation(BaseModel):
    work_id: str
    title: str


class User(BaseModel):
    user_id: int
    user_gender: str
    user_age: int
    user_zip: int
    user_firstname: str
    user_lastname: str
    user_firstlastname: str


class Purchase(BaseModel):
    user_id: int
    work_id: int
    total_purchases: int
    timestamp: Optional[str] = None


class Rating(BaseModel):
    user_id: int
    work_id: int
    rating: int
    timestamp: Optional[str] = None


class PageView(BaseModel):
    user_id: int
    work_id: int
    total_page_views: int


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
    file_path: str
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


class ProductProfileUpsert(BaseModel):
    """Only the fields needed for content-based/hybrid/session similarity - price,
    stock and images stay in the caller's own system, never sent here."""
    title: str
    description: Optional[str] = None
    genre_1: Optional[str] = None
    author: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None
    price: Optional[float] = None


class PurchaseEvent(BaseModel):
    user_id: int
    work_id: int
    quantity: int = 1
    occurred_at: Optional[datetime] = None


class ViewEvent(BaseModel):
    user_id: int
    work_id: int
    occurred_at: Optional[datetime] = None


class InteractionEvent(BaseModel):
    """Body for POST /events/{event_type} - generic across purchase, view, or any
    tenant-defined event type (a reservation, a watch, ...). `quantity` is the event's
    magnitude in whatever unit makes sense for that type (units bought, seconds
    watched, page count, ...) - it linearly scales the event's weight in training."""
    user_id: int
    work_id: int
    quantity: int = 1
    occurred_at: Optional[datetime] = None


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
