"""Placement rules one_per_group and closest_color: one item per model, never the current
item's model, each model in its colour closest to the current item's."""
from colors import color_distance, parse_color
from conftest import Q
from placement_filters import apply_variant_rules
from test_placements import create_pdp_placement


def test_colours_from_names_and_codes():
    assert parse_color("#2E7D32") == (46, 125, 50)
    assert parse_color("2e7d32") == (46, 125, 50)
    assert parse_color("Vert") == parse_color("verde") == parse_color("GREEN")
    assert parse_color("bordeaux") == parse_color("burgundy")
    assert parse_color("vert foncé") == parse_color("vert")  # a known name inside
    assert parse_color(["navy", "white"]) == parse_color("marine")
    assert parse_color("") is None and parse_color(None) is None and parse_color("xyz") is None
    brown, cognac, blue = parse_color("marron"), parse_color("cognac"), parse_color("bleu")
    assert color_distance(brown, cognac) < color_distance(brown, blue)


def item(item_id, group=None, color=None, title=None):
    props = {k: v for k, v in {"group_id": group, "color": color}.items() if v is not None}
    return {"item_id": item_id, "title": title or item_id, "score": 1.0, "properties": props, "explanation": None}


def rules(**extra):
    return {"business_rules": {"one_per_group": True, **extra}}


def test_one_per_model_never_the_current_one():
    ranked = [item("goya-black", "goya"), item("ibiza-red", "ibiza"), item("goya-red", "goya"), item("solo"), item("ibiza-blue", "ibiza")]
    current = item("goya-green", "goya", "vert")
    kept = apply_variant_rules(ranked, placement=rules(), current=current, variants_for=lambda g: {})
    assert [i["item_id"] for i in kept] == ["ibiza-red", "solo"]
    # Off by default: nothing changes.
    assert apply_variant_rules(ranked, placement={}, current=current, variants_for=lambda g: {}) == ranked


def test_the_cart_models_are_left_out_too():
    ranked = [item("goya-camel", "goya"), item("ibiza-red", "ibiza"), item("ranelagh-rose", "ranelagh")]
    kept = apply_variant_rules(ranked, placement=rules(), current=None, variants_for=lambda g: {}, cart=[item("goya-verde", "goya")])
    assert [i["item_id"] for i in kept] == ["ibiza-red", "ranelagh-rose"]


def test_title_groups_items_with_the_same_title():
    ranked = [item("a", title="IBIZA"), item("b", title=" ibiza "), item("c", title="GOYA")]
    kept = apply_variant_rules(ranked, placement=rules(group_by="title"), current=item("x", title="Goya"), variants_for=lambda g: {})
    assert [i["item_id"] for i in kept] == ["a"]


def test_each_model_in_its_closest_colour():
    ranked = [item("ibiza-black", "ibiza", "noir"), item("ranelagh-red", "ranelagh", "rouge")]
    variants = {
        "ibiza": [item("ibiza-black", "ibiza", "noir"), item("ibiza-olive", "ibiza", "kaki"), item("ibiza-blue", "ibiza", "bleu")],
        "ranelagh": [item("ranelagh-red", "ranelagh", "rouge")],
    }
    current = item("goya-green", "goya", "verde")
    shown = apply_variant_rules(ranked, placement=rules(closest_color=True), current=current, variants_for=lambda g: variants)
    assert [i["item_id"] for i in shown] == ["ibiza-olive", "ranelagh-red"]
    assert shown[0]["score"] == 1.0  # keeps the rank the model earned
    # Current item without a colour: the ranked variants stay.
    plain = apply_variant_rules(ranked, placement=rules(closest_color=True), current=item("goya", "goya"), variants_for=lambda g: variants)
    assert [i["item_id"] for i in plain] == ["ibiza-black", "ranelagh-red"]


BAGS = [
    ("goya-verde", "GOYA", "verde", 5), ("goya-negro", "GOYA", "negro", 5), ("goya-marron", "GOYA", "marron", 5),
    ("ibiza-negro", "IBIZA", "negro", 5), ("ibiza-kaki", "IBIZA", "kaki", 0), ("ibiza-verde-oscuro", "IBIZA", "verde oscuro", 5),
    ("castellana-burgundy", "CASTELLANA", "burgundy", 5), ("castellana-marron", "CASTELLANA", "marron", 5),
    ("ranelagh-rojo", "RANELAGH", "rojo", 5),
]


def test_through_the_api_like_a_shop_with_one_product_per_colour(http, tenant):
    for item_id, title, color, stock in BAGS:
        response = http.put(f"/items/{item_id}", params=Q, headers=tenant.secret, json={
            "title": title, "description": f"{title.lower()} leather handbag",
            "properties": {"category": "bags", "color": color, "stock": stock},
        })
        assert response.status_code == 201, response.text
    create_pdp_placement(
        http, tenant, slug="other-models", strategy="popular", limit=4,
        filters={"in_stock_only": True},
        business_rules={"one_per_group": True, "group_by": "title", "closest_color": True},
    )
    for route, headers in (("recommend", tenant.public), ("preview", tenant.secret)):
        response = http.post(f"/placements/other-models/{route}", headers=headers, json={"context": {"current_item_id": "goya-verde"}})
        assert response.status_code == 200, response.text
        ids = [i["item_id"] for i in response.json()["items"]]
        titles = [i["title"] for i in response.json()["items"]]
        assert "GOYA" not in titles, (route, ids)  # never the current model
        assert len(titles) == len(set(titles)) == 3, (route, ids)  # one per model, 3 other models
        assert "ibiza-verde-oscuro" in ids  # closest to green, and in stock (kaki is out)
        assert "ranelagh-rojo" in ids
        assert any(i.startswith("castellana-") for i in ids)


def test_cart_block_never_offers_another_colour_of_a_bag_in_the_cart(http, tenant):
    for item_id, title, color, stock in BAGS:
        http.put(f"/items/{item_id}", params=Q, headers=tenant.secret, json={"title": title, "properties": {"color": color, "stock": stock}})
    create_pdp_placement(
        http, tenant, slug="cart-models", context_type="cart", strategy="popular", limit=4,
        signals={"required": [], "optional": ["cart_item_ids"]},
        business_rules={"exclude_cart_items": True, "one_per_group": True, "group_by": "title"},
    )
    response = http.post("/placements/cart-models/recommend", headers=tenant.public, json={"context": {"cart_item_ids": ["goya-verde", "ranelagh-rojo"]}})
    assert response.status_code == 200, response.text
    titles = [i["title"] for i in response.json()["items"]]
    assert titles and not {"GOYA", "RANELAGH"} & set(titles)
    assert len(titles) == len(set(titles))
