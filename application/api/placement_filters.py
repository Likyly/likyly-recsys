"""Post-hoc filters/business rules for a Placement - applied to already-presented items
(the public RecommendedItem shape: item_id, properties, ...), after the strategy has already
ranked them. Deliberately simple (per the brief: no merchandising engine yet) - a handful of
attribute-based include/exclude rules plus a couple of behavioral ones, not a rules DSL.

`filters` (attribute-based): category_in, category_not_in, in_stock_only, include_attributes,
exclude_attributes.
`business_rules` (behavioral): exclude_current_item (default True), exclude_cart_items.
"""
from typing import Any


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
