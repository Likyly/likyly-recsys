# LIKYLY SDK for Python

The official Python client for the [LIKYLY](https://likyly.com) recommendations API. Send your catalog and what your visitors do, get personalised recommendations back.

```
catalog (items)  →  events  →  recommendations  →  impression → click → purchase
```

## Install

```sh
pip install likyly
```

Python 3.9 or later. Depends on `httpx`. Fully typed (`py.typed`). Sync (`Likyly`) and asyncio (`AsyncLikyly`) clients with the same methods.

## Quick start

### Initialize

Create one client and reuse it. Use your **secret** key on your server (see [Which key?](#which-key)).

```python
import os

from likyly import Likyly, NotFoundError, RateLimitError

# Backend only: the secret key gives access to your catalog and your users.
likyly = Likyly(
    api_key=os.environ["LIKYLY_SECRET_KEY"],
)
```

### Send your catalog

```python
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
```

Ids are your own: any string (`SKU-123`, a UUID, `gid://shopify/Product/123`). `properties` is free-form; `category` and `description` improve similarity, everything else is stored as is.

### Track events

```python
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
```

### Get recommendations

```python
# Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
recs = likyly.recommendations.get(user_id="user_123", placement="homepage", limit=3)

print(f"strategy: {recs.strategy}")
for item in recs.items:
    print(item.item_id, item.title)
```

You never choose an algorithm: LIKYLY picks the best strategy for what it knows about the visitor and tells you which one it used (`strategy`: `hybrid`, `content`, `collaborative`, `session` or `popular`). With no user, no session and no item you still get a useful answer: what is popular.

### Close the loop

```python
# Send the recommendation_id back: LIKYLY measures which recommendations get seen, clicked and bought.
likyly.events.impression(user_id="user_123", item_id=recs.items[0].item_id, recommendation_id=recs.recommendation_id, placement="homepage")
likyly.events.click(user_id="user_123", item_id=recs.items[0].item_id, recommendation_id=recs.recommendation_id, placement="homepage")
```

Send the recommendation id back on the events that follow (`impression` when it is displayed, `click`, `add_to_cart`, `purchase`) and LIKYLY can measure which recommendations actually convert.

## Which key?

| | Secret key | Public key |
|---|---|---|
| Where | your server only | anywhere, including a web page |
| Recommendations | yes | yes |
| Record events | yes | yes |
| Items and users (create, read, delete) | yes | no (`PermissionDeniedError`) |
| `debug` explanations | yes | no |

The public key can only read recommendations and write events, so it is safe to expose. **Never put the secret key in code that ships to a browser or a mobile app.**

```python
# Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
front = Likyly(
    api_key=os.environ["LIKYLY_PUBLIC_KEY"],
)

# A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
front.events.view(session_id="sess_abc", item_id="SKU-2")
for_visitor = front.recommendations.get(session_id="sess_abc", viewed_item_ids=["SKU-2"], placement="product_page", limit=3)
```

## Events

`track` is the one mechanism; `impression`, `view`, `click`, `add_to_cart`, `remove_from_cart` and `purchase` are shortcuts that call it with the matching type. Event types are **open strings**: use your own (`favorite`, `share`, `video_played`, ...) as freely as the official ones.

```python
# Any string is a valid event type: track what matters to your business.
likyly.events.track("favorite", user_id="user_123", item_id="SKU-2", properties={"list": "wishlist"})

# Up to 1000 events per call, each with its own type.
likyly.events.track_many([
    {"type": "view", "user_id": "user_123", "item_id": "SKU-3"},
    {"type": "add_to_cart", "user_id": "user_123", "item_id": "SKU-3", "quantity": 1},
])
```

- An event needs an item and a user, a session, or both (both ties the anonymous session to the user once they log in).
- Anonymous visitors: generate a session id (any string) and keep it in a cookie; no login needed.
- `properties` is a free-form object (price, currency, order id, ...). Its keys are stored exactly as you send them and are never renamed by the SDK.
- `event_id` (idempotency key): replaying an event with the same id records nothing and returns `duplicate: true`. Set it on purchases, for example `purchase_<orderId>_<itemId>`.
- `placement` is a free-form label of where something happened or will be displayed (`homepage`, `product_page`, `cart`, ...).

## Advanced recommendations

`recommendations.get` is the normal way. When you want to pick the strategy yourself:

```python
# Advanced Recommendations: one strategy at a time, when you want to choose.
similar = likyly.recommendations.similar(item_id="SKU-1", limit=3)
hybrid = likyly.recommendations.hybrid(user_id="user_123", item_id="SKU-1", alpha=0.7, limit=3)
session = likyly.recommendations.session(viewed_item_ids=["SKU-1", "SKU-2"], limit=3)
```

| Strategy | What it does |
|---|---|
| `popular` | The most popular items: the fallback for a visitor with no history |
| `similar` | Items similar to one item (content similarity), no user needed |
| `collaborative` | What users with similar histories liked. Needs a trained model, otherwise a not-found error |
| `hybrid` | Similar items personalised for a user; `alpha` (0 to 1) weighs collaborative against content |
| `session` | Recency-weighted, from `viewed item ids` or from a user's own view history (exactly one) |

These endpoints carry ids in the URL path, so ids containing `/` (such as `gid://shopify/Product/1`) or, for the session list, `,` are refused with a validation error: use `recommendations.get`, which takes ids in the request body.

## Errors

Errors are typed (every one is a `LikylyError`). Catch the specific ones you care about:

| Error | When |
|---|---|
| `ValidationError` | The request is invalid: caught by the SDK before anything is sent, or a 422 from the API (the message names the offending fields) |
| `AuthenticationError` | 401: missing, invalid or revoked API key |
| `PermissionDeniedError` | 403: for example a public key used for a secret-key operation, or a plan limit |
| `NotFoundError` | 404 |
| `RateLimitError` | 429: see the retry-after value |
| `ApiError` | Any other error status from the API (the four rows above are its subtypes) |
| `NetworkError` | No HTTP response: DNS, connection reset, TLS |
| `RequestTimeoutError` | The request timed out |

Errors that come from the API carry `.status_code`, `.request_id`, `.retry_after` (seconds), `.body`. Quote the request id when you contact support.

```python
try:
    likyly.items.get("does-not-exist")
except NotFoundError as error:
    print("no such item, request", error.request_id)
except RateLimitError as error:
    print("slow down, retry in", error.retry_after, "s")
```

## Retries and timeouts

The SDK retries transient failures for you (2 retries by default, exponential backoff from 0.5 s capped at 8 s, with full jitter):

- **429**: always retried, honouring `Retry-After`. A `Retry-After` longer than 60 s is not waited for: the rate-limit error is returned instead.
- **502, 503, 504, dropped connections and timeouts**: retried only when replaying the call is harmless: reads, upserts, deletes, events that carry an `event_id`, and batches where every event has one.
- An event **without** an `event_id` is never retried after an ambiguous failure, so a flaky network can never record a purchase twice. Set `event_id` on events you care about and they become safe to retry.
- Other 4xx errors are never retried.

## Configuration

| Setting | Option | Default |
|---|---|---|
| API key | `api_key` | required |
| Base URL | `base_url` | `https://api.likyly.com` |
| Catalog | `catalog` | your only catalog |
| Timeout | `timeout` (seconds) | 10 |
| Retries | `max_retries` | 2 |
| User-Agent suffix | `user_agent` | none |
| Custom HTTP | `http_client` (an `httpx.Client` / `httpx.AsyncClient`) | a new client |

```python
tuned = Likyly(
    api_key=os.environ["LIKYLY_SECRET_KEY"],
    timeout=5.0,  # seconds per attempt (default 10)
    max_retries=3,  # automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
    user_agent="my-shop/1.4",  # appended to the SDK's User-Agent
)
```

Every method takes a keyword-only `options=RequestOptions(timeout=2.0, max_retries=0)`.

## API reference

`key` says which API key may call it: **S** = secret key only, **P** = public or secret key.

| Method | What it does | Key |
|---|---|---|
| `likyly.items.get` | One item by your own id | S |
| `likyly.items.list` | One page of the catalog (`limit` / `offset`), with the catalog's total | S |
| `likyly.items.upsert` | Create or replace an item (idempotent) | S |
| `likyly.items.delete` | Remove an item (events already recorded for it are kept) | S |
| `likyly.items.upsert_many` | Batch upsert, 1 to 1000 items | S |
| `likyly.items.import_` | Alias of the batch upsert above (JSON batch) | S |
| `likyly.items.delete_many` | Batch delete, 1 to 1000 ids; unknown ids are reported, not raised | S |
| `likyly.users.get` | One user profile | S |
| `likyly.users.list` | One page of users (`limit` / `offset`) | S |
| `likyly.users.upsert` | Create or replace a profile (free-form `properties`) | S |
| `likyly.users.delete` | Erase the profile **and every event recorded for that user** | S |
| `likyly.users.import_` | Batch upsert of users, 1 to 1000 | S |
| `likyly.events.track` | Record an event of any type (open string) | P |
| `likyly.events.track_many` | Up to 1000 events in one call, each with its own type | P |
| `likyly.events.impression` | The item was displayed to the visitor | P |
| `likyly.events.view` | The visitor looked at the item | P |
| `likyly.events.click` | The visitor clicked the item | P |
| `likyly.events.add_to_cart` | The visitor added the item to their cart | P |
| `likyly.events.remove_from_cart` | The visitor removed the item from their cart | P |
| `likyly.events.purchase` | The visitor bought the item (set an event id) | P |
| `likyly.recommendations.get` | Recommendations with the automatic strategy: user, session, item or viewed items | P |
| `likyly.recommendations.popular` | Advanced: the most popular items | P |
| `likyly.recommendations.similar` | Advanced: items similar to one item | P |
| `likyly.recommendations.collaborative` | Advanced: what similar users liked (needs a trained model) | P |
| `likyly.recommendations.hybrid` | Advanced: similar items personalised for a user (`alpha`) | P |
| `likyly.recommendations.session` | Advanced: from viewed items or a user's view history | P |

Lists are paginated with `limit` and `offset` (the API's default page is 100 items); the total is reported next to the page. Users have a batch `import` but no batch delete: the API has no such endpoint. `import` on items is an alias of the batch upsert (the CSV import is a dashboard feature).

## Notes for Python

- Async code: `async with AsyncLikyly(api_key=...) as likyly:` then `await likyly.recommendations.get(...)`. Use `with Likyly(...) as likyly:` (or call `close()`) to release the connection pool.
- `import` is a Python keyword, so the batch import is `items.import_(...)` / `users.import_(...)`. Fields are `snake_case` (`item_id`, `recommendation_id`); the keys inside `properties` are yours and are never renamed.

## Documentation and support

- Documentation: <https://likyly.com/docs/sdk/python>
- REST API reference (OpenAPI): <https://api.likyly.com/docs>
- Package: likyly on PyPI

MIT licensed.
