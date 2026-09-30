"""Turns a raw source record into a NormalizedItem given a field_mapping, and helps a caller
(the /preview and /field-mapping/dry-run endpoints, and the MCP configure_field_mapping tool)
build that mapping in the first place: detect what fields a sample has, and suggest sane
defaults before a human or a coding agent confirms them.

A mapping value is a "path": a field name, or a dotted/bracket path into a nested record
(`variants[0].price`, `images[0].src`) - just enough for CSV/JSON/REST/Shopify-shaped data,
without pulling in a full JSONPath dependency for it.
"""
import re
from datetime import datetime
from typing import Any, Optional

from .base import NormalizedItem

_PATH_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+)\]")

# Recognized mapping keys - "attributes" is itself a dict of {attribute_name: path}, everything
# else maps directly to one NormalizedItem field.
NORMALIZED_FIELDS = (
    "external_id", "title", "description", "category", "price", "image",
    "url", "stock", "updated_at",
)

# Alias -> canonical mapping target, used by suggest_mapping. Order matters: earlier aliases
# for the same target win when a sample has more than one candidate field.
_ALIASES: dict[str, list[str]] = {
    "external_id": ["id", "external_id", "sku", "product_id", "item_id", "handle", "gid"],
    "title": ["title", "name", "product_name"],
    "description": ["description", "body_html", "summary", "desc"],
    "category": ["category", "categories", "product_type", "type", "genre", "collection"],
    "price": ["price", "variants[0].price", "amount", "unit_price"],
    "image": ["image", "image_url", "images[0].src", "thumbnail", "img", "picture"],
    "stock": ["stock", "inventory_quantity", "variants[0].inventory_quantity", "quantity", "stock_quantity"],
    "url": ["url", "link", "permalink", "handle"],
    "updated_at": ["updated_at", "modified_at", "date_modified", "last_modified"],
}


def resolve_path(record: dict[str, Any], path: Optional[str]) -> Any:
    """`resolve_path({"variants": [{"price": "9.99"}]}, "variants[0].price") == "9.99"`.
    Returns None for a missing key/index at any point, rather than raising - a mapping is
    allowed to name a field that a particular record happens not to have."""
    if not path:
        return None
    current: Any = record
    for name, index in _PATH_SEGMENT.findall(path):
        if current is None:
            return None
        if index != "":
            if not isinstance(current, (list, tuple)) or int(index) >= len(current):
                return None
            current = current[int(index)]
        else:
            if not isinstance(current, dict) or name not in current:
                return None
            current = current[name]
    return current


def detected_fields(sample: list[dict[str, Any]]) -> list[str]:
    """Union of top-level keys across the sample, in first-seen order - what a coding agent
    or a human sees before choosing a mapping."""
    seen: dict[str, None] = {}
    for record in sample:
        for key in record.keys():
            seen.setdefault(key, None)
    return list(seen.keys())


def suggest_mapping(sample: list[dict[str, Any]]) -> dict[str, Any]:
    """Best-effort default mapping from a sample's shape - a starting point a caller can
    accept as-is or override per field, never persisted on its own (configure_field_mapping /
    PUT .../field-mapping still has to be called to save it)."""
    if not sample:
        return {}
    fields = set(detected_fields(sample))
    mapping: dict[str, Any] = {}
    for target, candidates in _ALIASES.items():
        for candidate in candidates:
            top_level = candidate.split(".")[0].split("[")[0]
            if top_level in fields and resolve_path(sample[0], candidate) is not None:
                mapping[target] = candidate
                break
    return mapping


def _as_float(value: Any) -> Optional[float]:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return None if value is None or value == "" else int(float(value))
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _as_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_categories(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def apply_mapping(record: dict[str, Any], field_mapping: dict[str, Any]) -> NormalizedItem:
    """The one place a raw record turns into a NormalizedItem, for every connector - a
    connector's own normalize_item() just calls this with its type-specific default mapping
    merged under whatever the tenant configured (see e.g. connectors/shopify.py)."""
    external_id = resolve_path(record, field_mapping.get("external_id"))
    title = resolve_path(record, field_mapping.get("title"))
    if external_id in (None, ""):
        raise ValueError("field_mapping.external_id resolved to no value for this record")
    if title in (None, ""):
        raise ValueError("field_mapping.title resolved to no value for this record")

    attributes = {
        name: resolve_path(record, path)
        for name, path in (field_mapping.get("attributes") or {}).items()
    }
    return NormalizedItem(
        external_id=str(external_id),
        title=str(title),
        description=_as_str(resolve_path(record, field_mapping.get("description"))),
        categories=_as_categories(resolve_path(record, field_mapping.get("category"))),
        attributes={k: v for k, v in attributes.items() if v is not None},
        price=_as_float(resolve_path(record, field_mapping.get("price"))),
        image=_as_str(resolve_path(record, field_mapping.get("image"))),
        url=_as_str(resolve_path(record, field_mapping.get("url"))),
        stock=_as_int(resolve_path(record, field_mapping.get("stock"))),
        updated_at=_as_datetime(resolve_path(record, field_mapping.get("updated_at"))),
        raw_metadata=record,
    )
