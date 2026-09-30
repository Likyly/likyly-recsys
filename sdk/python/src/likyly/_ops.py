"""What each SDK call means, independent of how it is sent: request builders and response parsers.

Shared by the sync and the async client, so the two can never drift apart.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Generic, List, Mapping, Optional, TypeVar
from urllib.parse import quote

from .errors import ValidationError
from .models import (
    BatchError,
    BatchResult,
    EventBatchResult,
    EventResult,
    Explanation,
    Item,
    ItemList,
    RecommendationResponse,
    RecommendedItem,
    SimilarUser,
    User,
    UserList,
)

DEFAULT_LIMIT = 10
_EVENT_TYPE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class Response:
    status: int
    headers: Mapping[str, str]  # keys lower-cased
    data: Any


T = TypeVar("T")


@dataclass
class Spec(Generic[T]):
    method: str
    path: str  # already percent-encoded
    parse: Callable[[Response], T]
    query: Dict[str, Any] = field(default_factory=dict)
    body: Optional[Dict[str, Any]] = None
    #: Safe to send again if the outcome is unknown? False for events without an event_id.
    idempotent: bool = True


@dataclass
class RequestOptions:
    """Per-call overrides."""
    timeout: Optional[float] = None
    max_retries: Optional[int] = None


# ---- validation / encoding ----------------------------------------------------------------------

def encode_segment(value: str) -> str:
    """Percent-encodes everything outside RFC 3986 unreserved (A-Z a-z 0-9 - . _ ~), '/' included."""
    return quote(value, safe="")


def require_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f'{name} must be a non-empty string (your own identifier, e.g. "SKU-123")')
    return value


def require_path_safe_id(value: Any, name: str) -> str:
    id_ = require_id(value, name)
    if "/" in id_:
        raise ValidationError(
            f'{name} "{id_}" contains "/", which this endpoint cannot carry in its URL path - '
            "use recommendations.get(), which takes ids in the request body"
        )
    return id_


def _compact(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _limit(limit: Optional[int]) -> int:
    value = DEFAULT_LIMIT if limit is None else limit
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationError("limit must be a positive integer")
    return value


def _opt(v: Any) -> Any:
    return v


# ---- items --------------------------------------------------------------------------------------

def _item(w: Dict[str, Any]) -> Item:
    return Item(item_id=w["item_id"], title=w["title"], description=w.get("description"), properties=w.get("properties") or {})


def _total(headers: Mapping[str, str]) -> Optional[int]:
    v = headers.get("x-total-count")
    return int(v) if v is not None else None


def _item_body(title: Any, description: Optional[str], properties: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(title, str) or not title:
        raise ValidationError("an item needs a non-empty title")
    return _compact({"title": title, "description": description, "properties": properties})


def _batch(r: Response) -> BatchResult:
    d = r.data
    return BatchResult(
        received=d["received"], succeeded=d["succeeded"], failed=d["failed"],
        errors=[BatchError(index=e["index"], id=e.get("id"), message=e["message"]) for e in d.get("errors") or []],
    )


def items_get(item_id: str) -> "Spec[Item]":
    return Spec("GET", f"/items/{encode_segment(require_id(item_id, 'item_id'))}", lambda r: _item(r.data))


def items_list(limit: Optional[int], offset: Optional[int]) -> "Spec[ItemList]":
    return Spec(
        "GET", "/items", lambda r: ItemList(items=[_item(w) for w in r.data], total=_total(r.headers), limit=limit, offset=offset or 0),
        query=_compact({"limit": limit, "offset": offset}),
    )


def items_upsert(item_id: str, title: str, description: Optional[str], properties: Optional[Dict[str, Any]]) -> "Spec[Item]":
    return Spec(
        "PUT", f"/items/{encode_segment(require_id(item_id, 'item_id'))}", lambda r: _item(r.data),
        body=_item_body(title, description, properties),
    )


def items_delete(item_id: str) -> "Spec[None]":
    return Spec("DELETE", f"/items/{encode_segment(require_id(item_id, 'item_id'))}", lambda r: None)


def items_upsert_many(items: List[Mapping[str, Any]]) -> "Spec[BatchResult]":
    if not items:
        raise ValidationError("items must be a non-empty list")
    body = [
        {"item_id": require_id(i.get("item_id"), "item_id"), **_item_body(i.get("title"), i.get("description"), i.get("properties"))}
        for i in items
    ]
    return Spec("POST", "/items/import", _batch, body={"items": body})


def items_delete_many(item_ids: List[str]) -> "Spec[BatchResult]":
    if not item_ids:
        raise ValidationError("item_ids must be a non-empty list")
    return Spec("POST", "/items/delete", _batch, body={"item_ids": [require_id(i, "item_id") for i in item_ids]})


# ---- users --------------------------------------------------------------------------------------

def _user(w: Dict[str, Any]) -> User:
    return User(user_id=w["user_id"], properties=w.get("properties") or {})


def users_get(user_id: str) -> "Spec[User]":
    return Spec("GET", f"/users/{encode_segment(require_id(user_id, 'user_id'))}", lambda r: _user(r.data))


def users_list(limit: Optional[int], offset: Optional[int]) -> "Spec[UserList]":
    return Spec(
        "GET", "/users", lambda r: UserList(users=[_user(w) for w in r.data], total=_total(r.headers), limit=limit, offset=offset or 0),
        query=_compact({"limit": limit, "offset": offset}),
    )


def users_upsert(user_id: str, properties: Optional[Dict[str, Any]]) -> "Spec[User]":
    return Spec("PUT", f"/users/{encode_segment(require_id(user_id, 'user_id'))}", lambda r: _user(r.data), body=_compact({"properties": properties}))


def users_delete(user_id: str) -> "Spec[None]":
    return Spec("DELETE", f"/users/{encode_segment(require_id(user_id, 'user_id'))}", lambda r: None)


def users_import(users: List[Mapping[str, Any]]) -> "Spec[BatchResult]":
    if not users:
        raise ValidationError("users must be a non-empty list")
    body = [{"user_id": require_id(u.get("user_id"), "user_id"), **_compact({"properties": u.get("properties")})} for u in users]
    return Spec("POST", "/users/import", _batch, body={"users": body})


# ---- events -------------------------------------------------------------------------------------

def _event_type(type_: Any) -> str:
    if not isinstance(type_, str) or not _EVENT_TYPE.match(type_):
        raise ValidationError('event type must be 1-64 characters of letters, digits, "_" or "-" (e.g. "view", "add_to_cart", "favorite")')
    return type_


def _event_body(
    item_id: Any, user_id: Optional[str], session_id: Optional[str], recommendation_id: Optional[str], placement: Optional[str],
    quantity: Optional[int], occurred_at: Any, properties: Optional[Dict[str, Any]], event_id: Optional[str],
) -> Dict[str, Any]:
    require_id(item_id, "item_id")
    if user_id is None and session_id is None:
        raise ValidationError("an event needs a user_id or a session_id (or both)")
    if user_id is not None:
        require_id(user_id, "user_id")
    if session_id is not None:
        require_id(session_id, "session_id")
    return _compact({
        "event_id": event_id, "user_id": user_id, "session_id": session_id, "item_id": item_id,
        "recommendation_id": recommendation_id, "placement": placement, "quantity": quantity,
        "occurred_at": occurred_at.isoformat() if isinstance(occurred_at, datetime) else occurred_at,
        "properties": properties,
    })


def _event_result(r: Response) -> EventResult:
    return EventResult(message=r.data["message"], event_id=r.data.get("event_id"), duplicate=r.data.get("duplicate") is True)


def events_track(type_: str, **event: Any) -> "Spec[EventResult]":
    body = _event_body(
        event.get("item_id"), event.get("user_id"), event.get("session_id"), event.get("recommendation_id"), event.get("placement"),
        event.get("quantity"), event.get("occurred_at"), event.get("properties"), event.get("event_id"),
    )
    return Spec("POST", f"/events/{encode_segment(_event_type(type_))}", _event_result, body=body, idempotent=event.get("event_id") is not None)


def events_track_many(events: List[Mapping[str, Any]]) -> "Spec[EventBatchResult]":
    if not events:
        raise ValidationError("events must be a non-empty list")
    body = []
    for e in events:
        one = _event_body(
            e.get("item_id"), e.get("user_id"), e.get("session_id"), e.get("recommendation_id"), e.get("placement"),
            e.get("quantity"), e.get("occurred_at"), e.get("properties"), e.get("event_id"),
        )
        body.append({"event_type": _event_type(e.get("type")), **one})
    return Spec(
        "POST", "/events/batch", lambda r: EventBatchResult(r.data["received"], r.data["accepted"], r.data["duplicates"]),
        body={"events": body}, idempotent=all(e.get("event_id") is not None for e in events),
    )


# ---- recommendations ----------------------------------------------------------------------------

def _explanation(w: Optional[Dict[str, Any]]) -> Optional[Explanation]:
    if not w:
        return None
    return Explanation(
        reason=w["reason"], content_similarity=w.get("content_similarity"), semantic_similarity=w.get("semantic_similarity"),
        popularity_score=w.get("popularity_score"), interaction_count=w.get("interaction_count"),
        interaction_label=w.get("interaction_label"), collaborative_score=w.get("collaborative_score"),
        source_item_ids=w.get("source_item_ids"),
        similar_users=[SimilarUser(u["user_id"], u.get("shared_item_ids") or []) for u in w["similar_users"]] if w.get("similar_users") else None,
    )


def _recommendation(r: Response) -> RecommendationResponse:
    d = r.data
    return RecommendationResponse(
        recommendation_id=d["recommendation_id"], strategy=d["strategy"], placement=d.get("placement"),
        items=[
            RecommendedItem(
                item_id=i["item_id"], score=i.get("score"), title=i.get("title"), description=i.get("description"),
                properties=i.get("properties") or {}, explanation=_explanation(i.get("explanation")),
            )
            for i in d["items"]
        ],
    )


def recommendations_get(
    user_id: Optional[str], session_id: Optional[str], item_id: Optional[str], viewed_item_ids: Optional[List[str]],
    placement: Optional[str], limit: Optional[int], debug: Optional[bool],
) -> "Spec[RecommendationResponse]":
    body = _compact({
        "user_id": require_id(user_id, "user_id") if user_id is not None else None,
        "session_id": require_id(session_id, "session_id") if session_id is not None else None,
        "item_id": require_id(item_id, "item_id") if item_id is not None else None,
        "viewed_item_ids": [require_id(i, "viewed_item_ids[]") for i in viewed_item_ids] if viewed_item_ids is not None else None,
        "placement": placement, "count": limit, "debug": debug or None,
    })
    return Spec("POST", "/getRec", _recommendation, body=body)


def _advanced(path: str, placement: Optional[str], session_id: Optional[str], **extra: Any) -> "Spec[RecommendationResponse]":
    query = {"response_format": "object", **_compact({"placement": placement, "session_id": session_id, **extra})}
    return Spec("GET", path, _recommendation, query=query)


def recommendations_popular(limit: Optional[int], placement: Optional[str], session_id: Optional[str]) -> "Spec[RecommendationResponse]":
    return _advanced(f"/getRec/popular/{_limit(limit)}", placement, session_id)


def recommendations_similar(item_id: str, limit: Optional[int], placement: Optional[str], session_id: Optional[str]) -> "Spec[RecommendationResponse]":
    return _advanced(f"/getRec/content/{encode_segment(require_path_safe_id(item_id, 'item_id'))}/{_limit(limit)}", placement, session_id)


def recommendations_collaborative(user_id: str, limit: Optional[int], placement: Optional[str], session_id: Optional[str]) -> "Spec[RecommendationResponse]":
    return _advanced(f"/getRec/collaborative/{encode_segment(require_path_safe_id(user_id, 'user_id'))}/{_limit(limit)}", placement, session_id)


def recommendations_hybrid(user_id: str, item_id: str, limit: Optional[int], alpha: Optional[float], placement: Optional[str], session_id: Optional[str]) -> "Spec[RecommendationResponse]":
    path = f"/getRec/hybrid/{encode_segment(require_path_safe_id(user_id, 'user_id'))}/{encode_segment(require_path_safe_id(item_id, 'item_id'))}/{_limit(limit)}"
    return _advanced(path, placement, session_id, alpha=alpha)


def recommendations_session(
    viewed_item_ids: Optional[List[str]], user_id: Optional[str], limit: Optional[int], placement: Optional[str], session_id: Optional[str],
) -> "Spec[RecommendationResponse]":
    if (viewed_item_ids is not None) == (user_id is not None):
        raise ValidationError("session() needs either viewed_item_ids or user_id (exactly one)")
    if user_id is not None:
        return _advanced(f"/getRec/sessionForUser/{encode_segment(require_path_safe_id(user_id, 'user_id'))}/{_limit(limit)}", placement, session_id)
    ids = [require_id(i, "viewed_item_ids[]") for i in (viewed_item_ids or [])]
    if not ids:
        raise ValidationError("viewed_item_ids must contain at least one item id")
    if any("," in i for i in ids):
        raise ValidationError('an item id containing "," cannot be sent in this endpoint\'s comma-separated list - use recommendations.get()')
    return _advanced("/getRec/session", placement, session_id, viewed_item_ids=",".join(ids), count=_limit(limit))
