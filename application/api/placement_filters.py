"""Post-hoc filters/business rules for a Placement - applied to already-presented items
(the public RecommendedItem shape: item_id, properties, ...), after the strategy has already
ranked them. Deliberately simple (per the brief: no merchandising engine yet) - a handful of
attribute-based include/exclude rules plus a couple of behavioral ones, not a rules DSL.

`filters` (attribute-based): category_in, category_not_in, in_stock_only, include_attributes,
exclude_attributes.
`business_rules` (behavioral): exclude_current_item (default True), exclude_cart_items.

Variants (apply_variant_rules, after the filters): `one_per_group` shows one item per model -
items sharing the same `group_by` property (default `group_id`; "title" groups items with the
same title) count once, and the models of the current item and of the cart are left out. With it, `closest_color`
shows, of each model, the variant whose colour (`color_attribute`, default `color`) is closest
to the current item's. An item without the property is its own model; without a colour on
either side, the ranked variant stays.
"""
import re
from typing import Any, Callable, Optional

from colors import color_distance, parse_color


def _properties(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("properties") or {}


def _is_in_stock(item: dict[str, Any]) -> bool:
    """Only filters when a stock-ish property is actually present - "in-stock only si le
    champ existe" - an item with neither field is kept, never excluded for lacking data it
    was never asked to send."""
    props = _properties(item)
    if "in_stock" in props:
        return bool(props["in_stock"])
    if "stock" in props:
        try:
            return float(props["stock"]) > 0
        except (TypeError, ValueError):
            return True  # unparseable - don't punish the item for a data-quality issue
    return True


def apply_filters(items: list[dict[str, Any]], *, placement: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    filters = placement.get("filters") or {}
    business_rules = placement.get("business_rules") or {}
    result = items

    if business_rules.get("exclude_current_item", True) and context.get("current_item_id"):
        result = [i for i in result if i["item_id"] != context["current_item_id"]]

    if business_rules.get("exclude_cart_items") and context.get("cart_item_ids"):
        excluded = set(context["cart_item_ids"])
        result = [i for i in result if i["item_id"] not in excluded]

    # An explicit filters.category_in wins; otherwise a category_id in the context implicitly
    # scopes results to it (e.g. a category-page placement with no configured category_in) -
    # never both at once, so a placement author's own choice always takes priority.
    category_in = filters.get("category_in") or ([context["category_id"]] if context.get("category_id") else None)
    if category_in:
        allowed = set(category_in)
        result = [i for i in result if _properties(i).get("category") in allowed]

    if filters.get("category_not_in"):
        excluded = set(filters["category_not_in"])
        result = [i for i in result if _properties(i).get("category") not in excluded]

    if filters.get("in_stock_only"):
        result = [i for i in result if _is_in_stock(i)]

    for rule in filters.get("include_attributes") or []:
        attribute, values = rule.get("attribute"), set(rule.get("values") or [])
        if attribute:
            result = [i for i in result if _properties(i).get(attribute) in values]

    for rule in filters.get("exclude_attributes") or []:
        attribute, values = rule.get("attribute"), set(rule.get("values") or [])
        if attribute:
            result = [i for i in result if _properties(i).get(attribute) not in values]

    return result


def normalize_group(value: Any) -> Optional[str]:
    """The same model however it is written: case and spaces ignored. None when absent."""
    if value is None or isinstance(value, (dict, list)):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().casefold()
    return text or None


def group_of(item: dict[str, Any], group_by: str) -> Optional[str]:
    if group_by == "title":
        return normalize_group(item.get("title"))
    return normalize_group(_properties(item).get(group_by))


def _color_of(item: dict[str, Any], color_attribute: str):
    props = _properties(item)
    return parse_color(props.get(color_attribute)) or parse_color(props.get("color_hex"))


def apply_variant_rules(
    items: list[dict[str, Any]], *, placement: dict[str, Any], current: Optional[dict[str, Any]],
    variants_for: Callable[[list[str]], dict[str, list[dict[str, Any]]]],
    cart: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """One item per model, in the ranked order, never the current item's model; with
    closest_color, each model shown in its variant closest to the current item's colour.
    `current` is the current item (presented shape) or None; `cart` the cart's items, whose
    models are left out too; `variants_for(groups)` returns, for each model, its items already
    filtered like `items` (in stock, not in the cart...)."""
    rules = placement.get("business_rules") or {}
    if not rules.get("one_per_group"):
        return items
    group_by = rules.get("group_by") or "group_id"
    color_attribute = rules.get("color_attribute") or "color"
    excluded = {g for g in (group_of(i, group_by) for i in [current, *(cart or [])] if i) if g is not None}

    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        group = group_of(item, group_by)
        if group is not None and (group in excluded or group in seen):
            continue
        if group is not None:
            seen.add(group)
        kept.append(item)

    target = _color_of(current, color_attribute) if current and rules.get("closest_color") else None
    if target is None:
        return kept
    groups = [g for g in (group_of(i, group_by) for i in kept) if g is not None]
    variants = variants_for(groups) if groups else {}
    shown = {i["item_id"] for i in kept}
    result = []
    for item in kept:
        group = group_of(item, group_by)
        best, best_distance = item, None
        own = _color_of(item, color_attribute)
        if own is not None:
            best_distance = color_distance(own, target)
        for variant in variants.get(group, []) if group is not None else []:
            color = _color_of(variant, color_attribute)
            if color is None or variant["item_id"] in shown:
                continue
            distance = color_distance(color, target)
            if best_distance is None or distance < best_distance:
                best, best_distance = variant, distance
        if best is not item:
            shown.discard(item["item_id"])
            shown.add(best["item_id"])
            # Keeps the rank (score, explanation) the model earned; shows its closest variant.
            best = {**best, "score": item.get("score"), "explanation": item.get("explanation")}
        result.append(best)
    return result
