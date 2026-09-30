# LIKYLY SDK for Java

The official Java client for the [LIKYLY](https://likyly.com) recommendations API. Send your catalog and what your visitors do, get personalised recommendations back.

```
catalog (items)  →  events  →  recommendations  →  impression → click → purchase
```

## Install

```xml
<dependency>
  <groupId>com.likyly</groupId>
  <artifactId>likyly-sdk</artifactId>
  <version>1.0.0</version>
</dependency>
```

Java 17 or later. Uses the JDK's built-in `java.net.http.HttpClient` and Jackson for JSON. Thread-safe.

## Quick start

The snippets are statements: put them in a method (`main`, a service, a Spring bean...). Calls are blocking.

### Initialize

Create one client and reuse it. Use your **secret** key on your server (see [Which key?](#which-key)).

```java
import com.likyly.Likyly;
import com.likyly.error.NotFoundException;
import com.likyly.error.RateLimitException;
import com.likyly.model.EventInput;
import com.likyly.model.HybridOptions;
import com.likyly.model.ItemImport;
import com.likyly.model.RecommendationRequest;
import com.likyly.model.RecommendationResponse;
import com.likyly.model.SessionOptions;
import com.likyly.model.SimilarOptions;
import com.likyly.model.TypedEventInput;
import com.likyly.model.UserInput;
import java.time.Duration;
import java.util.List;
import java.util.Map;

// Backend only: the secret key gives access to your catalog and your users.
Likyly likyly = Likyly.builder()
    .apiKey(System.getenv("LIKYLY_SECRET_KEY"))
    .build();
```

### Send your catalog

```java
// Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
likyly.items().upsertMany(List.of(
    new ItemImport("SKU-1", "Nike Air Max", "Running shoe with air cushioning", Map.of("category", "shoes", "brand", "Nike", "price", 129.9)),
    new ItemImport("SKU-2", "Adidas Ultraboost", "Responsive running shoe", Map.of("category", "shoes", "brand", "Adidas", "price", 149)),
    new ItemImport("SKU-3", "Nike Pegasus", "Everyday running shoe", Map.of("category", "shoes", "brand", "Nike", "price", 119))));

// Users are optional: describe them if you want the profile to travel with their events.
likyly.users().upsert("user_123", new UserInput(Map.of("country", "FR", "segment", "premium")));
```

Ids are your own: any string (`SKU-123`, a UUID, `gid://shopify/Product/123`). `properties` is free-form; `category` and `description` improve similarity, everything else is stored as is.

### Track events

```java
// Tell LIKYLY what your visitors do.
likyly.events().view(EventInput.builder().userId("user_123").itemId("SKU-1").build());

// Purchases carry an eventId: replaying the call can never count the sale twice.
likyly.events().purchase(EventInput.builder()
    .eventId("purchase_order_9281_SKU-1")
    .userId("user_123")
    .itemId("SKU-1")
    .quantity(1)
    .properties(Map.of("price", 129.9, "currency", "EUR", "orderId", "order_9281"))
    .build());
```

### Get recommendations

```java
// Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
RecommendationResponse recs = likyly.recommendations().get(
    RecommendationRequest.builder().userId("user_123").placement("homepage").limit(3).build());

System.out.println("strategy: " + recs.strategy());
recs.items().forEach(item -> System.out.println(item.itemId() + " " + item.title()));
```

You never choose an algorithm: LIKYLY picks the best strategy for what it knows about the visitor and tells you which one it used (`strategy`: `hybrid`, `content`, `collaborative`, `session` or `popular`). With no user, no session and no item you still get a useful answer: what is popular.

### Close the loop

```java
// Send the recommendationId back: LIKYLY measures which recommendations get seen, clicked and bought.
EventInput shown = EventInput.builder()
    .userId("user_123")
    .itemId(recs.items().get(0).itemId())
    .recommendationId(recs.recommendationId())
    .placement("homepage")
    .build();
likyly.events().impression(shown);
likyly.events().click(shown);
```

Send the recommendation id back on the events that follow (`impression` when it is displayed, `click`, `add_to_cart`, `purchase`) and LIKYLY can measure which recommendations actually convert.

## Which key?

| | Secret key | Public key |
|---|---|---|
| Where | your server only | anywhere, including a web page |
| Recommendations | yes | yes |
| Record events | yes | yes |
| Items and users (create, read, delete) | yes | no (`PermissionDeniedException`) |
| `debug` explanations | yes | no |

The public key can only read recommendations and write events, so it is safe to expose. **Never put the secret key in code that ships to a browser or a mobile app.**

```java
// Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
Likyly front = Likyly.builder()
    .apiKey(System.getenv("LIKYLY_PUBLIC_KEY"))
    .build();

// A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
front.events().view(EventInput.builder().sessionId("sess_abc").itemId("SKU-2").build());
RecommendationResponse forVisitor = front.recommendations().get(
    RecommendationRequest.builder().sessionId("sess_abc").viewedItemIds(List.of("SKU-2")).placement("product_page").limit(3).build());
```

## Events

`track` is the one mechanism; `impression`, `view`, `click`, `add_to_cart`, `remove_from_cart` and `purchase` are shortcuts that call it with the matching type. Event types are **open strings**: use your own (`favorite`, `share`, `video_played`, ...) as freely as the official ones.

```java
// Any string is a valid event type: track what matters to your business.
likyly.events().track("favorite", EventInput.builder().userId("user_123").itemId("SKU-2").properties(Map.of("list", "wishlist")).build());

// Up to 1000 events per call, each with its own type.
likyly.events().trackMany(List.of(
    TypedEventInput.of("view", EventInput.builder().userId("user_123").itemId("SKU-3").build()),
    TypedEventInput.of("add_to_cart", EventInput.builder().userId("user_123").itemId("SKU-3").quantity(1).build())));
```

- An event needs an item and a user, a session, or both (both ties the anonymous session to the user once they log in).
- Anonymous visitors: generate a session id (any string) and keep it in a cookie; no login needed.
- `properties` is a free-form object (price, currency, order id, ...). Its keys are stored exactly as you send them and are never renamed by the SDK.
- `event_id` (idempotency key): replaying an event with the same id records nothing and returns `duplicate: true`. Set it on purchases, for example `purchase_<orderId>_<itemId>`.
- `placement` is a free-form label of where something happened or will be displayed (`homepage`, `product_page`, `cart`, ...).

## Advanced recommendations

`recommendations.get` is the normal way. When you want to pick the strategy yourself:

```java
// Advanced Recommendations: one strategy at a time, when you want to choose.
var similar = likyly.recommendations().similar(SimilarOptions.builder().itemId("SKU-1").limit(3).build());
var hybrid = likyly.recommendations().hybrid(HybridOptions.builder().userId("user_123").itemId("SKU-1").alpha(0.7).limit(3).build());
var session = likyly.recommendations().session(SessionOptions.builder().viewedItemIds(List.of("SKU-1", "SKU-2")).limit(3).build());
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

Errors are typed (every one is a `LikylyException`). Catch the specific ones you care about:

| Error | When |
|---|---|
| `ValidationException` | The request is invalid: caught by the SDK before anything is sent, or a 422 from the API (the message names the offending fields) |
| `AuthenticationException` | 401: missing, invalid or revoked API key |
| `PermissionDeniedException` | 403: for example a public key used for a secret-key operation, or a plan limit |
| `NotFoundException` | 404 |
| `RateLimitException` | 429: see the retry-after value |
| `ApiException` | Any other error status from the API (the four rows above are its subtypes) |
| `NetworkException` | No HTTP response: DNS, connection reset, TLS |
| `TimeoutException` | The request timed out |

Errors that come from the API carry `statusCode()`, `requestId()`, `retryAfter()` (seconds), `body()`. Quote the request id when you contact support.

```java
try {
    likyly.items().get("does-not-exist");
} catch (NotFoundException e) {
    System.out.println("no such item, request " + e.requestId());
} catch (RateLimitException e) {
    System.out.println("slow down, retry in " + e.retryAfter());
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
| API key | `apiKey(...)` | required |
| Base URL | `baseUrl(...)` | `https://api.likyly.com` |
| Catalog | `catalog(...)` | your only catalog |
| Timeout | `timeout(Duration)` | 10 s |
| Retries | `maxRetries(...)` | 2 |
| User-Agent suffix | `userAgent(...)` | none |
| Custom HTTP | `httpClient(java.net.http.HttpClient)` or `transport(Transport)` | a shared `HttpClient` |

```java
Likyly tuned = Likyly.builder()
    .apiKey(System.getenv("LIKYLY_SECRET_KEY"))
    .timeout(Duration.ofSeconds(5)) // per attempt (default 10 s)
    .maxRetries(3) // automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
    .userAgent("my-shop/1.4") // appended to the SDK's User-Agent
    .build();
```

Configure timeout and retries on the client; build a second client for a different policy.

## API reference

`key` says which API key may call it: **S** = secret key only, **P** = public or secret key.

| Method | What it does | Key |
|---|---|---|
| `likyly.items().get` | One item by your own id | S |
| `likyly.items().list` | One page of the catalog (`limit` / `offset`), with the catalog's total | S |
| `likyly.items().upsert` | Create or replace an item (idempotent) | S |
| `likyly.items().delete` | Remove an item (events already recorded for it are kept) | S |
| `likyly.items().upsertMany` | Batch upsert, 1 to 1000 items | S |
| `likyly.items().importItems` | Alias of the batch upsert above (JSON batch) | S |
| `likyly.items().deleteMany` | Batch delete, 1 to 1000 ids; unknown ids are reported, not raised | S |
| `likyly.users().get` | One user profile | S |
| `likyly.users().list` | One page of users (`limit` / `offset`) | S |
| `likyly.users().upsert` | Create or replace a profile (free-form `properties`) | S |
| `likyly.users().delete` | Erase the profile **and every event recorded for that user** | S |
| `likyly.users().importUsers` | Batch upsert of users, 1 to 1000 | S |
| `likyly.events().track` | Record an event of any type (open string) | P |
| `likyly.events().trackMany` | Up to 1000 events in one call, each with its own type | P |
| `likyly.events().impression` | The item was displayed to the visitor | P |
| `likyly.events().view` | The visitor looked at the item | P |
| `likyly.events().click` | The visitor clicked the item | P |
| `likyly.events().addToCart` | The visitor added the item to their cart | P |
| `likyly.events().removeFromCart` | The visitor removed the item from their cart | P |
| `likyly.events().purchase` | The visitor bought the item (set an event id) | P |
| `likyly.recommendations().get` | Recommendations with the automatic strategy: user, session, item or viewed items | P |
| `likyly.recommendations().popular` | Advanced: the most popular items | P |
| `likyly.recommendations().similar` | Advanced: items similar to one item | P |
| `likyly.recommendations().collaborative` | Advanced: what similar users liked (needs a trained model) | P |
| `likyly.recommendations().hybrid` | Advanced: similar items personalised for a user (`alpha`) | P |
| `likyly.recommendations().session` | Advanced: from viewed items or a user's view history | P |

Lists are paginated with `limit` and `offset` (the API's default page is 100 items); the total is reported next to the page. Users have a batch `import` but no batch delete: the API has no such endpoint. `import` on items is an alias of the batch upsert (the CSV import is a dashboard feature).

## Notes for Java

- Models are immutable records with builders (`EventInput.builder()...build()`). Calls are blocking: run them on a virtual thread or an executor if you need concurrency.
- `import` is a Java keyword, so the batch imports are `items().importItems(...)` and `users().importUsers(...)`. The keys inside `properties` (a `Map<String, Object>`) are yours and are never renamed.

## Documentation and support

- Documentation: <https://likyly.com/docs/sdk/java>
- REST API reference (OpenAPI): <https://api.likyly.com/docs>
- Package: com.likyly:likyly-sdk on Maven Central

MIT licensed.
