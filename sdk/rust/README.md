# LIKYLY SDK for Rust

The official Rust client for the [LIKYLY](https://likyly.com) recommendations API. Send your catalog and what your visitors do, get personalised recommendations back.

```
catalog (items)  →  events  →  recommendations  →  impression → click → purchase
```

## Install

```sh
cargo add likyly tokio --features tokio/macros,tokio/rt-multi-thread
```

Async (Tokio). Built on `reqwest` with rustls, `serde` types for every request and response. The `Likyly` client is `Clone` and cheap to share.

## Quick start

Every call is async: the snippets below run inside `#[tokio::main] async fn main() -> Result<(), likyly::Error>`.

### Initialize

Create one client and reuse it. Use your **secret** key on your server (see [Which key?](#which-key)).

```rust
use std::time::Duration;

use likyly::{
    Error, EventInput, HybridOptions, ItemImport, Likyly, Properties, RecommendationRequest, SessionOptions, SimilarOptions, TypedEvent,
    UserInput,
};
use serde_json::json;

// Backend only: the secret key gives access to your catalog and your users.
let likyly = Likyly::builder(std::env::var("LIKYLY_SECRET_KEY").unwrap())
    .build()?;
```

### Send your catalog

```rust
// Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
let shoe = |id: &str, title: &str, description: &str, brand: &str, price: f64| ItemImport {
    item_id: id.into(),
    title: title.into(),
    description: Some(description.into()),
    properties: Some(props(json!({ "category": "shoes", "brand": brand, "price": price }))),
};
likyly
    .items()
    .upsert_many(&[
        shoe("SKU-1", "Nike Air Max", "Running shoe with air cushioning", "Nike", 129.9),
        shoe("SKU-2", "Adidas Ultraboost", "Responsive running shoe", "Adidas", 149.0),
        shoe("SKU-3", "Nike Pegasus", "Everyday running shoe", "Nike", 119.0),
    ])
    .await?;

// Users are optional: describe them if you want the profile to travel with their events.
likyly.users().upsert("user_123", UserInput { properties: Some(props(json!({ "country": "FR", "segment": "premium" }))) }).await?;
```

Ids are your own: any string (`SKU-123`, a UUID, `gid://shopify/Product/123`). `properties` is free-form; `category` and `description` improve similarity, everything else is stored as is.

### Track events

```rust
// Tell LIKYLY what your visitors do.
likyly.events().view(EventInput::for_user("user_123", "SKU-1")).await?;

// Purchases carry an event_id: replaying the call can never count the sale twice.
likyly
    .events()
    .purchase(EventInput {
        event_id: Some("purchase_order_9281_SKU-1".into()),
        quantity: Some(1),
        properties: Some(props(json!({ "price": 129.9, "currency": "EUR", "orderId": "order_9281" }))),
        ..EventInput::for_user("user_123", "SKU-1")
    })
    .await?;
```

### Get recommendations

```rust
// Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
let recs = likyly
    .recommendations()
    .get(RecommendationRequest {
        user_id: Some("user_123".into()),
        placement: Some("homepage".into()),
        limit: Some(3),
        ..Default::default()
    })
    .await?;

println!("strategy: {}", recs.strategy);
for item in &recs.items {
    println!("{} {}", item.item_id, item.title.as_deref().unwrap_or(""));
}
```

You never choose an algorithm: LIKYLY picks the best strategy for what it knows about the visitor and tells you which one it used (`strategy`: `hybrid`, `content`, `collaborative`, `session` or `popular`). With no user, no session and no item you still get a useful answer: what is popular.

### Close the loop

```rust
// Send the recommendation_id back: LIKYLY measures which recommendations get seen, clicked and bought.
let shown = EventInput {
    recommendation_id: Some(recs.recommendation_id.clone()),
    placement: Some("homepage".into()),
    ..EventInput::for_user("user_123", recs.items[0].item_id.clone())
};
likyly.events().impression(shown.clone()).await?;
likyly.events().click(shown).await?;
```

Send the recommendation id back on the events that follow (`impression` when it is displayed, `click`, `add_to_cart`, `purchase`) and LIKYLY can measure which recommendations actually convert.

## Which key?

| | Secret key | Public key |
|---|---|---|
| Where | your server only | anywhere, including a web page |
| Recommendations | yes | yes |
| Record events | yes | yes |
| Items and users (create, read, delete) | yes | no (`Error::PermissionDenied`) |
| `debug` explanations | yes | no |

The public key can only read recommendations and write events, so it is safe to expose. **Never put the secret key in code that ships to a browser or a mobile app.**

```rust
// Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
let front = Likyly::builder(std::env::var("LIKYLY_PUBLIC_KEY").unwrap())
    .build()?;

// A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
front.events().view(EventInput::for_session("sess_abc", "SKU-2")).await?;
let for_visitor = front
    .recommendations()
    .get(RecommendationRequest {
        session_id: Some("sess_abc".into()),
        viewed_item_ids: Some(vec!["SKU-2".into()]),
        placement: Some("product_page".into()),
        limit: Some(3),
        ..Default::default()
    })
    .await?;
```

## Events

`track` is the one mechanism; `impression`, `view`, `click`, `add_to_cart`, `remove_from_cart` and `purchase` are shortcuts that call it with the matching type. Event types are **open strings**: use your own (`favorite`, `share`, `video_played`, ...) as freely as the official ones.

```rust
// Any string is a valid event type: track what matters to your business.
likyly
    .events()
    .track(
        "favorite",
        EventInput { properties: Some(props(json!({ "list": "wishlist" }))), ..EventInput::for_user("user_123", "SKU-2") },
    )
    .await?;

// Up to 1000 events per call, each with its own type.
likyly
    .events()
    .track_many(&[
        TypedEvent::new("view", EventInput::for_user("user_123", "SKU-3")),
        TypedEvent::new("add_to_cart", EventInput { quantity: Some(1), ..EventInput::for_user("user_123", "SKU-3") }),
    ])
    .await?;
```

- An event needs an item and a user, a session, or both (both ties the anonymous session to the user once they log in).
- Anonymous visitors: generate a session id (any string) and keep it in a cookie; no login needed.
- `properties` is a free-form object (price, currency, order id, ...). Its keys are stored exactly as you send them and are never renamed by the SDK.
- `event_id` (idempotency key): replaying an event with the same id records nothing and returns `duplicate: true`. Set it on purchases, for example `purchase_<orderId>_<itemId>`.
- `placement` is a free-form label of where something happened or will be displayed (`homepage`, `product_page`, `cart`, ...).

## Advanced recommendations

`recommendations.get` is the normal way. When you want to pick the strategy yourself:

```rust
// Advanced Recommendations: one strategy at a time, when you want to choose.
let similar =
    likyly.recommendations().similar(SimilarOptions { item_id: "SKU-1".into(), limit: Some(3), ..Default::default() }).await?;
let hybrid = likyly
    .recommendations()
    .hybrid(HybridOptions {
        user_id: "user_123".into(),
        item_id: "SKU-1".into(),
        alpha: Some(0.7),
        limit: Some(3),
        ..Default::default()
    })
    .await?;
let session = likyly
    .recommendations()
    .session(SessionOptions { viewed_item_ids: Some(vec!["SKU-1".into(), "SKU-2".into()]), limit: Some(3), ..Default::default() })
    .await?;
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

Errors are typed (every one is a `likyly::Error`). Catch the specific ones you care about:

| Error | When |
|---|---|
| `Error::Validation` | The request is invalid: caught by the SDK before anything is sent, or a 422 from the API (the message names the offending fields) |
| `Error::Authentication` | 401: missing, invalid or revoked API key |
| `Error::PermissionDenied` | 403: for example a public key used for a secret-key operation, or a plan limit |
| `Error::NotFound` | 404 |
| `Error::RateLimit` | 429: see the retry-after value |
| `Error::Api` | Any other error status from the API (the four rows above are its subtypes) |
| `Error::Network` | No HTTP response: DNS, connection reset, TLS |
| `Error::Timeout` | The request timed out |

Errors that come from the API carry `status_code()`, `request_id()`, `retry_after()` (`Duration`), `body()`. Quote the request id when you contact support.

```rust
match likyly.items().get("does-not-exist").await {
    Ok(_) => {}
    Err(Error::NotFound(e)) => println!("no such item, request {:?}", e.request_id),
    Err(Error::RateLimit(e)) => println!("slow down, retry in {:?}", e.retry_after),
    Err(other) => return Err(other),
}
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
| API key | `Likyly::new(key)` / `Likyly::builder(key)` | required |
| Base URL | `.base_url(..)` | `https://api.likyly.com` |
| Catalog | `.catalog(..)` | your only catalog |
| Timeout | `.timeout(Duration)` | 10 s |
| Retries | `.max_retries(..)` | 2 |
| User-Agent suffix | `.user_agent(..)` | none |
| Custom HTTP | `.transport(Arc<dyn Transport>)`, or `ReqwestTransport::with_client(..)` | `reqwest` |

```rust
let tuned = Likyly::builder(std::env::var("LIKYLY_SECRET_KEY").unwrap())
    .timeout(Duration::from_secs(5)) // per attempt (default 10 s)
    .max_retries(3) // automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
    .user_agent("my-shop/1.4") // appended to the SDK's User-Agent
    .build()?;
```

`client.with_options(RequestOptions { timeout, max_retries })` returns a copy of the client with different limits; dropping a future cancels the call.

## API reference

`key` says which API key may call it: **S** = secret key only, **P** = public or secret key.

| Method | What it does | Key |
|---|---|---|
| `likyly.items().get` | One item by your own id | S |
| `likyly.items().list` | One page of the catalog (`limit` / `offset`), with the catalog's total | S |
| `likyly.items().upsert` | Create or replace an item (idempotent) | S |
| `likyly.items().delete` | Remove an item (events already recorded for it are kept) | S |
| `likyly.items().upsert_many` | Batch upsert, 1 to 1000 items | S |
| `likyly.items().import` | Alias of the batch upsert above (JSON batch) | S |
| `likyly.items().delete_many` | Batch delete, 1 to 1000 ids; unknown ids are reported, not raised | S |
| `likyly.users().get` | One user profile | S |
| `likyly.users().list` | One page of users (`limit` / `offset`) | S |
| `likyly.users().upsert` | Create or replace a profile (free-form `properties`) | S |
| `likyly.users().delete` | Erase the profile **and every event recorded for that user** | S |
| `likyly.users().import` | Batch upsert of users, 1 to 1000 | S |
| `likyly.events().track` | Record an event of any type (open string) | P |
| `likyly.events().track_many` | Up to 1000 events in one call, each with its own type | P |
| `likyly.events().impression` | The item was displayed to the visitor | P |
| `likyly.events().view` | The visitor looked at the item | P |
| `likyly.events().click` | The visitor clicked the item | P |
| `likyly.events().add_to_cart` | The visitor added the item to their cart | P |
| `likyly.events().remove_from_cart` | The visitor removed the item from their cart | P |
| `likyly.events().purchase` | The visitor bought the item (set an event id) | P |
| `likyly.recommendations().get` | Recommendations with the automatic strategy: user, session, item or viewed items | P |
| `likyly.recommendations().popular` | Advanced: the most popular items | P |
| `likyly.recommendations().similar` | Advanced: items similar to one item | P |
| `likyly.recommendations().collaborative` | Advanced: what similar users liked (needs a trained model) | P |
| `likyly.recommendations().hybrid` | Advanced: similar items personalised for a user (`alpha`) | P |
| `likyly.recommendations().session` | Advanced: from viewed items or a user's view history | P |

Lists are paginated with `limit` and `offset` (the API's default page is 100 items); the total is reported next to the page. Users have a batch `import` but no batch delete: the API has no such endpoint. `import` on items is an alias of the batch upsert (the CSV import is a dashboard feature).

## Notes for Rust

- `Error` is `#[non_exhaustive]`: keep a wildcard arm. The API-derived variants carry a boxed `ApiError` (`e.status_code`, `e.request_id`, `e.retry_after`).
- Request and response fields are `snake_case`; optional fields are `Option<_>`, and `..Default::default()` fills the rest. The keys inside `Properties` (a `serde_json::Map`) are yours and are never renamed.

## Documentation and support

- Documentation: <https://likyly.com/docs/sdk/rust>
- REST API reference (OpenAPI): <https://api.likyly.com/docs>
- Package: likyly on crates.io

MIT licensed.
