"""Public response types. Fields are ``snake_case``; ``properties`` dictionaries are yours, verbatim."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

Properties = Dict[str, Any]


@dataclass(frozen=True)
class Item:
    #: Your own identifier - any string (``SKU-123``, a UUID, ``gid://shopify/Product/123``).
    item_id: str
    title: str
    description: Optional[str] = None
    properties: Properties = field(default_factory=dict)


@dataclass(frozen=True)
class ItemList:
    items: List[Item]
    #: Total number of items in the catalog (``X-Total-Count``), when the API reported it.
    total: Optional[int] = None
    #: The page size you asked for (None = the API's default).
    limit: Optional[int] = None
    offset: int = 0


@dataclass(frozen=True)
class User:
    user_id: str
    properties: Properties = field(default_factory=dict)


@dataclass(frozen=True)
class UserList:
    users: List[User]
    total: Optional[int] = None
    limit: Optional[int] = None
    offset: int = 0


@dataclass(frozen=True)
class BatchError:
    index: int
    message: str
    id: Optional[str] = None


@dataclass(frozen=True)
class BatchResult:
    """A failing entry is reported in ``errors``, it does not raise."""
    received: int
    succeeded: int
    failed: int
    errors: List[BatchError] = field(default_factory=list)


@dataclass(frozen=True)
class EventResult:
    message: str
    #: True if this ``event_id`` was already recorded - nothing was written.
    duplicate: bool = False
    event_id: Optional[str] = None


@dataclass(frozen=True)
class EventBatchResult:
    received: int
    accepted: int
    #: Events skipped because their ``event_id`` was already recorded.
    duplicates: int


@dataclass(frozen=True)
class SimilarUser:
    user_id: str
    shared_item_ids: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Explanation:
    reason: str
    content_similarity: Optional[float] = None
    semantic_similarity: Optional[float] = None
    popularity_score: Optional[float] = None
    interaction_count: Optional[int] = None
    interaction_label: Optional[str] = None
    collaborative_score: Optional[float] = None
    source_item_ids: Optional[List[str]] = None
    #: Only with ``debug`` and a secret key.
    similar_users: Optional[List[SimilarUser]] = None


@dataclass(frozen=True)
class RecommendedItem:
    item_id: str
    score: Optional[float] = None
    title: Optional[str] = None
    description: Optional[str] = None
    properties: Properties = field(default_factory=dict)
    explanation: Optional[Explanation] = None


@dataclass(frozen=True)
class RecommendationResponse:
    #: Send it back on the impression / click / add_to_cart / purchase events for these items.
    recommendation_id: str
    #: What LIKYLY used: ``hybrid``, ``content``, ``collaborative``, ``session`` or ``popular``.
    strategy: str
    items: List[RecommendedItem]
    placement: Optional[str] = None
