"""The four resources: ``client.items``, ``client.users``, ``client.events``, ``client.recommendations``."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Mapping, Optional, Union

from . import _ops
from ._http import HttpClient
from ._ops import RequestOptions
from .models import (
    BatchResult,
    EventBatchResult,
    EventResult,
    Item,
    ItemList,
    Properties,
    RecommendationResponse,
    User,
    UserList,
)


class Items:
    """Your catalog. Needs the **secret** API key."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def get(self, item_id: str, *, options: Optional[RequestOptions] = None) -> Item:
        """One item by your own id."""
        return self._http.send(_ops.items_get(item_id), options)

    def list(self, *, limit: Optional[int] = None, offset: Optional[int] = None, options: Optional[RequestOptions] = None) -> ItemList:
        """One page of the catalog. Pagination is ``limit`` + ``offset``; ``total`` is the whole catalog's size."""
        return self._http.send(_ops.items_list(limit, offset), options)

    def upsert(
        self, item_id: str, *, title: str, description: Optional[str] = None, properties: Optional[Properties] = None,
        options: Optional[RequestOptions] = None,
    ) -> Item:
        """Creates the item, or replaces it if it exists - idempotent. Fields you leave out are cleared."""
        return self._http.send(_ops.items_upsert(item_id, title, description, properties), options)

    def delete(self, item_id: str, *, options: Optional[RequestOptions] = None) -> None:
        """Removes the item from the catalog. Events already recorded for it are kept."""
        self._http.send(_ops.items_delete(item_id), options)

    def upsert_many(self, items: List[Mapping[str, Any]], *, options: Optional[RequestOptions] = None) -> BatchResult:
        """Batch upsert (1-1000). Each dict: ``item_id``, ``title``, optional ``description`` / ``properties``."""
        return self._http.send(_ops.items_upsert_many(items), options)

    def import_(self, items: List[Mapping[str, Any]], *, options: Optional[RequestOptions] = None) -> BatchResult:
        """Alias of :meth:`upsert_many` (the API's ``POST /items/import`` is a JSON batch upsert)."""
        return self.upsert_many(items, options=options)

    def delete_many(self, item_ids: List[str], *, options: Optional[RequestOptions] = None) -> BatchResult:
        """Batch delete (1-1000). Ids that don't exist are reported in ``errors``."""
        return self._http.send(_ops.items_delete_many(item_ids), options)


class Users:
    """Optional user profiles. Needs the **secret** API key. You don't have to create a user before sending events for them."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def get(self, user_id: str, *, options: Optional[RequestOptions] = None) -> User:
        return self._http.send(_ops.users_get(user_id), options)

    def list(self, *, limit: Optional[int] = None, offset: Optional[int] = None, options: Optional[RequestOptions] = None) -> UserList:
        """Without ``limit`` the API returns every user."""
        return self._http.send(_ops.users_list(limit, offset), options)

    def upsert(self, user_id: str, *, properties: Optional[Properties] = None, options: Optional[RequestOptions] = None) -> User:
        """Creates or replaces the profile. ``properties`` is free-form: country, segment, language, ..."""
        return self._http.send(_ops.users_upsert(user_id, properties), options)

    def delete(self, user_id: str, *, options: Optional[RequestOptions] = None) -> None:
        """Erases the user: the profile **and every event recorded for them**."""
        self._http.send(_ops.users_delete(user_id), options)

    def import_(self, users: List[Mapping[str, Any]], *, options: Optional[RequestOptions] = None) -> BatchResult:
        """Batch upsert (1-1000). Each dict: ``user_id`` and optional ``properties``."""
        return self._http.send(_ops.users_import(users), options)


class Events:
    """What your visitors do. Works with the **public** key from a browser-facing service, or the secret key.

    ``track()`` is the one mechanism; ``view()``, ``click()``, ... call it with the matching event type.
    Event types are open strings - ``favorite``, ``share``, anything - the six helpers are conveniences.
    A failed call is only retried automatically when it carries an ``event_id``.
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def track(
        self, type: str, *, item_id: str, user_id: Optional[str] = None, session_id: Optional[str] = None,
        recommendation_id: Optional[str] = None, placement: Optional[str] = None, quantity: Optional[int] = None,
        occurred_at: Union[str, datetime, None] = None, properties: Optional[Properties] = None,
        event_id: Optional[str] = None, options: Optional[RequestOptions] = None,
    ) -> EventResult:
        """Needs ``item_id`` and a ``user_id`` and/or a ``session_id`` (anonymous visitor). ``properties`` keys are yours."""
        return self._http.send(_ops.events_track(
            type, item_id=item_id, user_id=user_id, session_id=session_id, recommendation_id=recommendation_id,
            placement=placement, quantity=quantity, occurred_at=occurred_at, properties=properties, event_id=event_id,
        ), options)

    def track_many(self, events: List[Mapping[str, Any]], *, options: Optional[RequestOptions] = None) -> EventBatchResult:
        """Up to 1000 events. Each dict has ``type`` plus the fields of :meth:`track`."""
        return self._http.send(_ops.events_track_many(events), options)

    def impression(self, **event: Any) -> EventResult:
        """The item was shown to the visitor (send the ``recommendation_id`` it came with)."""
        return self.track("impression", **event)

    def view(self, **event: Any) -> EventResult:
        """The visitor looked at the item (a product page, an article)."""
        return self.track("view", **event)

    def click(self, **event: Any) -> EventResult:
        return self.track("click", **event)

    def add_to_cart(self, **event: Any) -> EventResult:
        return self.track("add_to_cart", **event)

    def remove_from_cart(self, **event: Any) -> EventResult:
        return self.track("remove_from_cart", **event)

    def purchase(self, **event: Any) -> EventResult:
        """Set ``event_id`` (e.g. ``purchase_<order_id>_<item_id>``) so a retry can never count the purchase twice."""
        return self.track("purchase", **event)


class Recommendations:
    """Use :meth:`get`: send what you know and LIKYLY picks the best strategy.

    The other methods are the *Advanced Recommendations* - one strategy at a time.
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def get(
        self, *, user_id: Optional[str] = None, session_id: Optional[str] = None, item_id: Optional[str] = None,
        viewed_item_ids: Optional[List[str]] = None, placement: Optional[str] = None, limit: Optional[int] = None,
        debug: bool = False, options: Optional[RequestOptions] = None,
    ) -> RecommendationResponse:
        """Recommendations for a user, an anonymous session, an item being viewed, viewed items - or nothing at all
        (then you get what is popular). ``strategy`` in the response says what LIKYLY used; send ``recommendation_id``
        back on the events that follow. ``limit`` is 1-100 (default 10)."""
        return self._http.send(_ops.recommendations_get(user_id, session_id, item_id, viewed_item_ids, placement, limit, debug), options)

    # ---- Advanced Recommendations: ids travel in the URL path here, so they cannot contain "/" ----

    def popular(self, *, limit: Optional[int] = None, placement: Optional[str] = None, session_id: Optional[str] = None, options: Optional[RequestOptions] = None) -> RecommendationResponse:
        """The most popular items - the fallback for a visitor with no history at all."""
        return self._http.send(_ops.recommendations_popular(limit, placement, session_id), options)

    def similar(self, *, item_id: str, limit: Optional[int] = None, placement: Optional[str] = None, session_id: Optional[str] = None, options: Optional[RequestOptions] = None) -> RecommendationResponse:
        """Items similar to one item (content similarity). No user needed."""
        return self._http.send(_ops.recommendations_similar(item_id, limit, placement, session_id), options)

    def collaborative(self, *, user_id: str, limit: Optional[int] = None, placement: Optional[str] = None, session_id: Optional[str] = None, options: Optional[RequestOptions] = None) -> RecommendationResponse:
        """What users with similar histories liked. Needs a trained model."""
        return self._http.send(_ops.recommendations_collaborative(user_id, limit, placement, session_id), options)

    def hybrid(self, *, user_id: str, item_id: str, limit: Optional[int] = None, alpha: Optional[float] = None, placement: Optional[str] = None, session_id: Optional[str] = None, options: Optional[RequestOptions] = None) -> RecommendationResponse:
        """Similar items, personalized for a user. ``alpha`` (0-1) weighs the collaborative signal against content similarity."""
        return self._http.send(_ops.recommendations_hybrid(user_id, item_id, limit, alpha, placement, session_id), options)

    def session(self, *, viewed_item_ids: Optional[List[str]] = None, user_id: Optional[str] = None, limit: Optional[int] = None, placement: Optional[str] = None, session_id: Optional[str] = None, options: Optional[RequestOptions] = None) -> RecommendationResponse:
        """Recency-weighted recommendations from what was viewed: pass ``viewed_item_ids`` (oldest first) **or**
        ``user_id`` (LIKYLY's own history of that user's views) - exactly one."""
        return self._http.send(_ops.recommendations_session(viewed_item_ids, user_id, limit, placement, session_id), options)
