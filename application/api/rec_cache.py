"""In-process cache for anonymous placement recommendations.

For a visitor with no user_id, a placement's answer for one product depends only on the catalog
and the placement's configuration (the "content" strategy: the products closest to the current
one). Every anonymous visitor of that product page would make LIKYLY compute the same list:
this cache keeps it for TTL_SECONDS, so a busy page costs one computation an hour.

It empties itself, without anyone purging it:
- when the placement changes: its `version`/`updated_at` is part of the key;
- when the catalog changes: every item write (ingestion.py - the public API, imports and data
  source syncs all go through it) bumps that catalog's generation, part of the key too.
`purge()` empties it by hand (console button, MCP tool, API).

What is cached is the list of presented items, never the response: each call still mints its
own recommendation_id and is logged, so attribution and analytics are unchanged.

In-process on purpose: the API runs as a single process (see the Dockerfile). Behind several
workers or replicas this would need a shared store (Redis), as the rate limits in app.py note.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Hashable, Optional

TTL_SECONDS = 3600
MAX_ENTRIES = 50_000
# Strategies whose answer does not depend on who the anonymous visitor is.
CACHEABLE_STRATEGIES = {"content", "popular"}

_lock = threading.Lock()
_entries: "OrderedDict[Hashable, tuple[float, Any]]" = OrderedDict()
_catalog_generation: dict[tuple[int, str], int] = {}
_client_generation: dict[int, int] = {}
_stats = {"hits": 0, "misses": 0}


def is_cacheable_request(context: dict[str, Any], *, follows_session: bool = False) -> bool:
    """Anonymous calls only: a user_id may switch the strategy to hybrid/collaborative. On a
    plan whose answers follow the visitor's session (Pro), a call that names a session is that
    visitor's own and is not shared either."""
    if context.get("user_id"):
        return False
    return not (follows_session and (context.get("session_id") or context.get("anonymous_id")))


def key(client_id: int, placement: dict[str, Any], context: dict[str, Any], limit: int) -> tuple:
    product_type = placement.get("product_type") or ""
    # Context keys that change the answer (filters, category pages...), minus who the visitor is.
    shape = tuple(sorted((k, repr(v)) for k, v in context.items() if k not in {"session_id", "anonymous_id"}))
    return (
        client_id,
        placement["slug"],
        placement.get("version"),
        str(placement.get("updated_at")),
        _client_generation.get(client_id, 0),
        _catalog_generation.get((client_id, product_type), 0),
        limit,
        shape,
    )


def get(cache_key: tuple, *, now: Optional[float] = None) -> Any:
    now = time.monotonic() if now is None else now
    with _lock:
        hit = _entries.get(cache_key)
        if hit is None or hit[0] <= now:
            if hit is not None:
                del _entries[cache_key]
            _stats["misses"] += 1
            return None
        _entries.move_to_end(cache_key)
        _stats["hits"] += 1
        return hit[1]


def put(cache_key: tuple, value: Any, *, ttl: float = TTL_SECONDS, now: Optional[float] = None) -> None:
    now = time.monotonic() if now is None else now
    with _lock:
        _entries[cache_key] = (now + ttl, value)
        _entries.move_to_end(cache_key)
        while len(_entries) > MAX_ENTRIES:
            _entries.popitem(last=False)


def bump_catalog(client_id: int, product_type: str) -> None:
    """An item of this catalog was written or deleted: its cached answers are stale."""
    with _lock:
        k = (client_id, product_type)
        _catalog_generation[k] = _catalog_generation.get(k, 0) + 1


def purge(client_id: int, slug: Optional[str] = None) -> int:
    """Drop this account's cached answers (one placement's, or all). Returns how many."""
    with _lock:
        doomed = [k for k in _entries if k[0] == client_id and (slug is None or k[1] == slug)]
        for k in doomed:
            del _entries[k]
        if slug is None:
            _client_generation[client_id] = _client_generation.get(client_id, 0) + 1
        return len(doomed)


def count(client_id: int, slug: Optional[str] = None, *, now: Optional[float] = None) -> int:
    now = time.monotonic() if now is None else now
    with _lock:
        return sum(1 for k, (exp, _) in _entries.items() if k[0] == client_id and (slug is None or k[1] == slug) and exp > now)


def clear() -> None:
    """Tests only."""
    with _lock:
        _entries.clear()
        _catalog_generation.clear()
        _client_generation.clear()
        _stats.update(hits=0, misses=0)
