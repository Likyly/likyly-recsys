"""Quick start: catalog -> events -> recommendations -> attribution.

Run it: LIKYLY_SECRET_KEY=... LIKYLY_PUBLIC_KEY=... python examples/quickstart.py
"""

# region:imports
import os

from likyly import Likyly, NotFoundError, RateLimitError
# endregion

# region:initialize
# Backend only: the secret key gives access to your catalog and your users.
likyly = Likyly(
    api_key=os.environ["LIKYLY_SECRET_KEY"],
    base_url=os.environ.get("LIKYLY_BASE_URL"),  # docs:omit
    catalog=os.environ.get("LIKYLY_CATALOG"),  # docs:omit
)
# endregion

# region:catalog
# Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
likyly.items.upsert_many([
    {"item_id": "SKU-1", "title": "Nike Air Max", "description": "Running shoe with air cushioning",
     "properties": {"category": "shoes", "brand": "Nike", "price": 129.9}},
    {"item_id": "SKU-2", "title": "Adidas Ultraboost", "description": "Responsive running shoe",
     "properties": {"category": "shoes", "brand": "Adidas", "price": 149}},
    {"item_id": "SKU-3", "title": "Nike Pegasus", "description": "Everyday running shoe",
     "properties": {"category": "shoes", "brand": "Nike", "price": 119}},
])

# Users are optional: describe them if you want the profile to travel with their events.
likyly.users.upsert("user_123", properties={"country": "FR", "segment": "premium"})
# endregion

# region:track
# Tell LIKYLY what your visitors do.
likyly.events.view(user_id="user_123", item_id="SKU-1")

# Purchases carry an event_id: replaying the call can never count the sale twice.
likyly.events.purchase(
    event_id="purchase_order_9281_SKU-1",
    user_id="user_123",
    item_id="SKU-1",
    quantity=1,
    properties={"price": 129.9, "currency": "EUR", "orderId": "order_9281"},
)
# endregion

# region:recommend
# Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
recs = likyly.recommendations.get(user_id="user_123", placement="homepage", limit=3)

print(f"strategy: {recs.strategy}")
for item in recs.items:
    print(item.item_id, item.title)
# endregion

# region:showcase
# Les recommandations pour votre page : tout est optionnel, envoyez ce que vous savez.
product_recs = likyly.recommendations.get(
    user_id="user_123",  # visiteur connecté
    session_id="sess_abc",  # ou visiteur anonyme (cookie)
    item_id="SKU-1",  # fiche produit en cours de consultation
    placement="product_page",  # où elles seront affichées (libre)
    limit=6,  # combien d'articles (10 par défaut)
)

for item in product_recs.items:
    print(item.item_id, item.title, item.score)
# endregion

# region:attribution
# Send the recommendation_id back: LIKYLY measures which recommendations get seen, clicked and bought.
likyly.events.impression(user_id="user_123", item_id=recs.items[0].item_id, recommendation_id=recs.recommendation_id, placement="homepage")
likyly.events.click(user_id="user_123", item_id=recs.items[0].item_id, recommendation_id=recs.recommendation_id, placement="homepage")
# endregion

# region:browser
# Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
front = Likyly(
    api_key=os.environ["LIKYLY_PUBLIC_KEY"],
    base_url=os.environ.get("LIKYLY_BASE_URL"),  # docs:omit
    catalog=os.environ.get("LIKYLY_CATALOG"),  # docs:omit
)

# A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
front.events.view(session_id="sess_abc", item_id="SKU-2")
for_visitor = front.recommendations.get(session_id="sess_abc", viewed_item_ids=["SKU-2"], placement="product_page", limit=3)
# endregion

# region:custom
# Any string is a valid event type: track what matters to your business.
likyly.events.track("favorite", user_id="user_123", item_id="SKU-2", properties={"list": "wishlist"})

# Up to 1000 events per call, each with its own type.
likyly.events.track_many([
    {"type": "view", "user_id": "user_123", "item_id": "SKU-3"},
    {"type": "add_to_cart", "user_id": "user_123", "item_id": "SKU-3", "quantity": 1},
])
# endregion

# region:advanced
# Advanced Recommendations: one strategy at a time, when you want to choose.
similar = likyly.recommendations.similar(item_id="SKU-1", limit=3)
hybrid = likyly.recommendations.hybrid(user_id="user_123", item_id="SKU-1", alpha=0.7, limit=3)
session = likyly.recommendations.session(viewed_item_ids=["SKU-1", "SKU-2"], limit=3)
# endregion

# region:config
tuned = Likyly(
    api_key=os.environ["LIKYLY_SECRET_KEY"],
    timeout=5.0,  # seconds per attempt (default 10)
    max_retries=3,  # automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
    user_agent="my-shop/1.4",  # appended to the SDK's User-Agent
    base_url=os.environ.get("LIKYLY_BASE_URL"),  # docs:omit
)
# endregion

# region:errors
try:
    likyly.items.get("does-not-exist")
except NotFoundError as error:
    print("no such item, request", error.request_id)
except RateLimitError as error:
    print("slow down, retry in", error.retry_after, "s")
# endregion

print(f"visitor strategy: {for_visitor.strategy}")

# region:cleanup
likyly.items.delete_many(["SKU-1", "SKU-2", "SKU-3"])
likyly.users.delete("user_123")
# endregion
likyly.close()
front.close()
print("quickstart ok")
