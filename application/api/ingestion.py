"""Business logic behind the public items / users / events endpoints.

The route handlers in app.py stay thin (auth, params, HTTP status); everything that decides
what an upsert or an event *means* lives here, so the legacy endpoints (/products,
/events/view, ...) and the new ones (/items, /events/{event_type}, /events/batch) share one
implementation instead of two that drift apart.

Public ids (any string) become the engine's dense integers through db.id_map - see
db.resolve_internal_ids.
"""
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)


from db import (  # noqa: E402
    DEFAULT_EVENT_TIER, KIND_ITEM, KIND_USER, count_products_for_client, delete_product_profile,
    delete_user_data, fetch_products, fetch_user_rows, get_client_event_type_weights,
    get_client_plan, get_plan_limits, insert_interactions, product_exists,
    resolve_external_ids, resolve_internal_ids, upsert_client_event_type, upsert_product_profile,
    upsert_user, update_product_embedding, fetch_item_properties,
)
from ids import numeric_alias  # noqa: E402
from schemas import Event, ItemUpsert, UserUpsert  # noqa: E402


class PlanLimitError(Exception):
    """The account's plan doesn't allow this write (free-tier catalog cap)."""


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

_LEGACY_ITEM_FIELDS = (("genre_1", "category"), ("author", "author"), ("year", "year"), ("url", "url"), ("price", "price"))


def build_item_properties(payload: ItemUpsert) -> dict[str, Any]:
    """`properties` as stored: what the integrator sent, plus any deprecated top-level field
    folded in (genre_1 -> category) unless properties already says something for that key."""
    properties = dict(payload.properties)
    for legacy_name, property_name in _LEGACY_ITEM_FIELDS:
        value = getattr(payload, legacy_name)
        if value is not None and property_name not in properties:
            properties[property_name] = value
    return properties


def _first_present(properties: dict, *keys: str) -> Any:
    for key in keys:
        value = properties.get(key)
        if value not in (None, ""):
            return value
    return None


def derive_item_columns(properties: dict[str, Any]) -> dict[str, Any]:
    """The few structured columns the engine itself reads (genre_1 for diversification,
    author/year for the similarity text, url/price for display), derived from the free-form
    properties. A value of the wrong type (year: "n/a") is ignored rather than failing the
    upsert - `properties` keeps it verbatim either way."""
    def as_int(value):
        try:
            return None if isinstance(value, bool) or value is None else int(float(value))
        except (TypeError, ValueError):
            return None

    def as_float(value):
        try:
            return None if isinstance(value, bool) or value is None else float(value)
        except (TypeError, ValueError):
            return None

    def as_str(value):
        return None if value is None else str(value)

    return {
        "genre_1": as_str(_first_present(properties, "category", "genre", "genre_1")),
        "author": as_str(_first_present(properties, "author")),
        "year": as_int(_first_present(properties, "year")),
        "url": as_str(_first_present(properties, "url")),
        "price": as_float(_first_present(properties, "price")),
    }


def _check_plan_limit(client_id: int, new_items: int) -> None:
    """Only brand-new items count against the cap - updating an existing one's profile must
    stay possible even once the free-tier catalog is full."""
    if new_items <= 0:
        return
    product_limit = get_plan_limits(get_client_plan(client_id))["product_limit"]
    if product_limit is not None and count_products_for_client(client_id) + new_items > product_limit:
        raise PlanLimitError(f"Free plan limit reached: {product_limit} products max. Upgrade your plan to add more products.")


def upsert_item(client_id: int, product_type: str, item_id: str, payload: ItemUpsert) -> bool:
    """Create or replace an item (PUT semantics: idempotent, the payload is the whole item).
    Returns True if it was created."""
    internal = resolve_internal_ids(client_id, product_type, KIND_ITEM, [item_id], create=True)[item_id]
    is_new = not product_exists(client_id, product_type, internal)
    if is_new:
        _check_plan_limit(client_id, 1)

    properties = build_item_properties(payload)
    columns = derive_item_columns(properties)
    created = upsert_product_profile(
        product_type=product_type, work_id=internal, title=payload.title,
        description=payload.description, client_id=client_id, properties=properties, **columns,
    )
    _store_embedding(client_id, product_type, internal, payload.title, payload.description, columns["genre_1"])
    return created


def _embedding_text(title: str, description: Optional[str], genre_1: Optional[str]) -> str:
    return " ".join(filter(None, [title, description, genre_1]))


def _store_embedding(client_id: int, product_type: str, work_id: int, title: str, description: Optional[str], genre_1: Optional[str]) -> None:
    try:
        from modelData import compute_embedding
        update_product_embedding(client_id, product_type, work_id, compute_embedding(_embedding_text(title, description, genre_1)))
    except Exception as error:
        # The profile itself is already saved and content-based (TF-IDF) recs work fine
        # without an embedding - a failure here shouldn't fail the whole upsert.
        print(f"Embedding computation skipped (non-fatal): {error}")


@dataclass
class BatchOutcome:
    received: int
    succeeded: int
    errors: list[dict[str, Any]]


def upsert_items_batch(client_id: int, product_type: str, entries: list) -> BatchOutcome:
    """Each entry behaves like PUT /items/{item_id}; a plan-limit refusal is reported on that
    entry (later entries that are updates of existing items still go through)."""
    external_ids = [e.item_id for e in entries]
    mapping = resolve_internal_ids(client_id, product_type, KIND_ITEM, external_ids, create=True)

    errors: list[dict[str, Any]] = []
    to_embed: list[tuple[int, str, str, Optional[str], Optional[str]]] = []
    product_limit = get_plan_limits(get_client_plan(client_id))["product_limit"]
    current_count = count_products_for_client(client_id) if product_limit is not None else 0

    succeeded = 0
    for index, entry in enumerate(entries):
        internal = mapping[entry.item_id]
        is_new = not product_exists(client_id, product_type, internal)
        if is_new and product_limit is not None and current_count >= product_limit:
            errors.append({"index": index, "id": entry.item_id, "message": f"Free plan limit reached: {product_limit} products max"})
            continue
        properties = build_item_properties(entry)
        columns = derive_item_columns(properties)
        upsert_product_profile(
            product_type=product_type, work_id=internal, title=entry.title, description=entry.description,
            client_id=client_id, properties=properties, **columns,
        )
        if is_new:
            current_count += 1
        succeeded += 1
        to_embed.append((internal, entry.title, _embedding_text(entry.title, entry.description, columns["genre_1"]), entry.description, columns["genre_1"]))

    if to_embed:
        try:
            from modelData import compute_embeddings
            vectors = compute_embeddings([text for _, _, text, _, _ in to_embed])
            for (internal, *_), vector in zip(to_embed, vectors):
                update_product_embedding(client_id, product_type, internal, vector)
        except Exception as error:
            print(f"Embedding computation skipped (non-fatal): {error}")
    return BatchOutcome(len(entries), succeeded, errors)


def delete_items(client_id: int, product_type: str, item_ids: list[str]) -> list[str]:
    """Returns the item_ids that did not exist. Only the catalog profile is removed - the
    interactions recorded for the item stay (they are still valid collaborative signal, and
    the item may come back)."""
    mapping = resolve_internal_ids(client_id, product_type, KIND_ITEM, item_ids)
    missing = []
    for item_id in item_ids:
        internal = mapping.get(item_id)
        if internal is None or not delete_product_profile(product_type=product_type, work_id=internal, client_id=client_id):
            missing.append(item_id)
    return missing


def fetch_items(
    client_id: int, product_type: str, *, item_id: Optional[str] = None,
    limit: Optional[int] = None, offset: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Catalog rows as dicts: item_id (external), the structured columns, and `properties` -
    stored, or synthesized from the columns for rows written before the column existed.
    An unknown item_id yields []."""
    work_id = None
    if item_id is not None:
        work_id = resolve_internal_ids(client_id, product_type, KIND_ITEM, [item_id]).get(item_id)
        if work_id is None:
            return []

    df = fetch_products(product_type, work_id=work_id, count=limit, client_id=client_id, offset=offset)
    if df.empty:
        return []
    records = json.loads(df.to_json(orient="records"))
    external = resolve_external_ids(client_id, product_type, KIND_ITEM, [r["work_id"] for r in records])
    stored = fetch_item_properties(client_id, product_type, [r["work_id"] for r in records])

    from recommender import item_properties_for
    items = []
    for record in records:
        ext = external.get(record["work_id"], str(record["work_id"]))
        year = record.get("year")
        items.append({
            "item_id": ext, "work_id": numeric_alias(ext), "title": record["title"],
            "description": record.get("description"), "genre_1": record.get("genre_1"),
            "author": record.get("author"), "year": int(year) if year is not None else None,
            "url": record.get("url"), "price": record.get("price"),
            "properties": item_properties_for(record, stored.get(record["work_id"])),
        })
    return items


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

_USER_LEGACY_TO_PROPERTY = {
    "user_gender": "gender", "user_age": "age", "user_zip": "zip",
    "user_firstname": "firstname", "user_lastname": "lastname",
}


def build_user_properties(payload: UserUpsert) -> dict[str, Any]:
    properties = dict(payload.properties)
    for legacy_name, property_name in _USER_LEGACY_TO_PROPERTY.items():
        value = getattr(payload, legacy_name)
        if value is not None and property_name not in properties:
            properties[property_name] = value
    return properties


def derive_user_columns(properties: dict[str, Any]) -> dict[str, Any]:
    """Legacy profile columns, derived from `properties` - only used for display (a
    similar-user's name) and by the Solara demo pages."""
    def as_int(value):
        try:
            return None if isinstance(value, bool) or value is None else int(float(value))
        except (TypeError, ValueError):
            return None

    def as_str(value):
        return None if value is None else str(value)

    return {
        "user_gender": as_str(_first_present(properties, "gender")),
        "user_age": as_int(_first_present(properties, "age")),
        "user_zip": as_int(_first_present(properties, "zip")),
        "user_firstname": as_str(_first_present(properties, "firstname", "first_name")),
        "user_lastname": as_str(_first_present(properties, "lastname", "last_name")),
    }


def upsert_user_profile(client_id: int, product_type: str, user_id: str, payload: UserUpsert) -> bool:
    """PUT semantics - the payload replaces the whole profile. True if newly created."""
    internal = resolve_internal_ids(client_id, product_type, KIND_USER, [user_id], create=True)[user_id]
    properties = build_user_properties(payload)
    return upsert_user(
        product_type=product_type, user_id=internal, client_id=client_id,
        properties=properties, **derive_user_columns(properties),
    )


def upsert_users_batch(client_id: int, product_type: str, entries: list) -> BatchOutcome:
    mapping = resolve_internal_ids(client_id, product_type, KIND_USER, [e.user_id for e in entries], create=True)
    errors: list[dict[str, Any]] = []
    succeeded = 0
    for index, entry in enumerate(entries):
        try:
            properties = build_user_properties(entry)
            upsert_user(
                product_type=product_type, user_id=mapping[entry.user_id], client_id=client_id,
                properties=properties, **derive_user_columns(properties),
            )
            succeeded += 1
        except Exception as error:
            errors.append({"index": index, "id": entry.user_id, "message": str(error)})
    return BatchOutcome(len(entries), succeeded, errors)


def _user_row_to_public(row: dict[str, Any], external_id: str) -> dict[str, Any]:
    properties = row["properties"]
    if properties is None:
        synthesized = {prop: row[legacy] for legacy, prop in _USER_LEGACY_TO_PROPERTY.items()}
        properties = {k: v for k, v in synthesized.items() if v is not None}
    first, last = row["user_firstname"], row["user_lastname"]
    return {
        "user_id": external_id, "properties": properties,
        "user_gender": row["user_gender"], "user_age": row["user_age"], "user_zip": row["user_zip"],
        "user_firstname": first, "user_lastname": last,
        "user_firstlastname": f"{first} {last}" if first and last else None,
    }


def fetch_users(
    client_id: int, product_type: str, *, user_id: Optional[str] = None,
    limit: Optional[int] = None, offset: Optional[int] = None,
) -> list[dict[str, Any]]:
    internal = None
    if user_id is not None:
        internal = resolve_internal_ids(client_id, product_type, KIND_USER, [user_id]).get(user_id)
        if internal is None:
            return []
    rows = fetch_user_rows(client_id, product_type, user_id=internal, limit=limit, offset=offset)
    external = resolve_external_ids(client_id, product_type, KIND_USER, [r["user_id"] for r in rows])
    return [_user_row_to_public(r, external.get(r["user_id"], str(r["user_id"]))) for r in rows]


def delete_user(client_id: int, product_type: str, user_id: str) -> bool:
    internal = resolve_internal_ids(client_id, product_type, KIND_USER, [user_id]).get(user_id)
    if internal is None:
        return False
    return delete_user_data(client_id, product_type, internal)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@dataclass
class EventOutcome:
    accepted: int
    duplicates: int
    # True if at least one event of a weighted (signal-carrying) type was accepted - the
    # caller uses it to decide whether an auto-retrain check is worth doing.
    carries_signal: bool


def ensure_event_types(client_id: int, event_types: set[str]) -> dict[str, float]:
    """type -> training weight for every type in the set, auto-registering any this tenant
    hasn't used before (default tier; the tenant can retune label/tier from the dashboard)."""
    weights = get_client_event_type_weights(client_id)
    for event_type in sorted(event_types - weights.keys()):
        upsert_client_event_type(client_id, event_type, event_type.replace("_", " ").title(), DEFAULT_EVENT_TIER)
        weights = get_client_event_type_weights(client_id)
    return weights


def record_events(client_id: int, product_type: str, events: list[tuple[str, Event]]) -> EventOutcome:
    """The one path every event takes - purchase/view routes, the generic route, the batch
    route, the CSV import. Resolves public ids to internal ones (creating the mapping for an
    id seen for the first time), then writes all rows in one statement; a row whose event_id
    was already recorded is skipped, which is what makes a retried purchase harmless."""
    if not events:
        return EventOutcome(0, 0, False)

    weights = ensure_event_types(client_id, {event_type for event_type, _ in events})
    items = resolve_internal_ids(client_id, product_type, KIND_ITEM, [e.item_id for _, e in events if e.item_id], create=True)
    users = resolve_internal_ids(client_id, product_type, KIND_USER, [e.user_id for _, e in events if e.user_id], create=True)

    # Event's own validator guarantees item_id (and user_id or session_id) - restated here for
    # the type checker and as a guard for a caller that builds an Event with model_construct.
    assert all(event.item_id is not None for _, event in events), "events must carry an item_id"
    rows = [
        {
            "client_id": client_id, "product_type": product_type, "event_type": event_type,
            "work_id": items[event.item_id],  # type: ignore[index]
            "user_id": users[event.user_id] if event.user_id else None,
            "session_id": event.session_id, "quantity": event.quantity,
            "occurred_at": event.occurred_at, "event_id": event.event_id,
            "recommendation_id": event.recommendation_id, "placement": event.placement,
            "properties": event.properties or None,
        }
        for event_type, event in events
    ]
    accepted = insert_interactions(rows)
    carries_signal = accepted > 0 and any(weights.get(event_type, 0) > 0 for event_type, _ in events)
    return EventOutcome(accepted, len(events) - accepted, carries_signal)
