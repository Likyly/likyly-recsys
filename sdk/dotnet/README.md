# LIKYLY SDK for .NET

The official .NET client for the [LIKYLY](https://likyly.com) recommendations API. Send your catalog and what your visitors do, get personalised recommendations back.

```
catalog (items)  →  events  →  recommendations  →  impression → click → purchase
```

## Install

```sh
dotnet add package Likyly
```

.NET 8 or later. No dependency beyond the framework (`HttpClient`, `System.Text.Json`). Async everywhere, `CancellationToken` on every call.

## Quick start

The snippets are top-level statements (`Program.cs`); in a class, put them in an `async` method.

### Initialize

Create one client and reuse it. Use your **secret** key on your server (see [Which key?](#which-key)).

```csharp
using Likyly;

// Backend only: the secret key gives access to your catalog and your users.
var likyly = new LikylyClient(new LikylyClientOptions
{
    ApiKey = Environment.GetEnvironmentVariable("LIKYLY_SECRET_KEY")!,
});
```

### Send your catalog

```csharp
// Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
await likyly.Items.UpsertManyAsync(
[
    new ItemImport { ItemId = "SKU-1", Title = "Nike Air Max", Description = "Running shoe with air cushioning", Properties = new Dictionary<string, object?> { ["category"] = "shoes", ["brand"] = "Nike", ["price"] = 129.9 } },
    new ItemImport { ItemId = "SKU-2", Title = "Adidas Ultraboost", Description = "Responsive running shoe", Properties = new Dictionary<string, object?> { ["category"] = "shoes", ["brand"] = "Adidas", ["price"] = 149 } },
    new ItemImport { ItemId = "SKU-3", Title = "Nike Pegasus", Description = "Everyday running shoe", Properties = new Dictionary<string, object?> { ["category"] = "shoes", ["brand"] = "Nike", ["price"] = 119 } },
]);

// Users are optional: describe them if you want the profile to travel with their events.
await likyly.Users.UpsertAsync("user_123", new UserInput { Properties = new Dictionary<string, object?> { ["country"] = "FR", ["segment"] = "premium" } });
```

Ids are your own: any string (`SKU-123`, a UUID, `gid://shopify/Product/123`). `properties` is free-form; `category` and `description` improve similarity, everything else is stored as is.

### Track events

```csharp
// Tell LIKYLY what your visitors do.
await likyly.Events.ViewAsync(new EventInput { UserId = "user_123", ItemId = "SKU-1" });

// Purchases carry an EventId: replaying the call can never count the sale twice.
await likyly.Events.PurchaseAsync(new EventInput
{
    EventId = "purchase_order_9281_SKU-1",
    UserId = "user_123",
    ItemId = "SKU-1",
    Quantity = 1,
    Properties = new Dictionary<string, object?> { ["price"] = 129.9, ["currency"] = "EUR", ["orderId"] = "order_9281" },
});
```

### Get recommendations

```csharp
// Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
var recs = await likyly.Recommendations.GetAsync(new RecommendationRequest { UserId = "user_123", Placement = "homepage", Limit = 3 });

Console.WriteLine($"strategy: {recs.Strategy}");
foreach (var item in recs.Items)
{
    Console.WriteLine($"{item.ItemId} {item.Title}");
}
```

You never choose an algorithm: LIKYLY picks the best strategy for what it knows about the visitor and tells you which one it used (`strategy`: `hybrid`, `content`, `collaborative`, `session` or `popular`). With no user, no session and no item you still get a useful answer: what is popular.

### Close the loop

```csharp
// Send the RecommendationId back: LIKYLY measures which recommendations get seen, clicked and bought.
var shown = new EventInput { UserId = "user_123", ItemId = recs.Items[0].ItemId, RecommendationId = recs.RecommendationId, Placement = "homepage" };
await likyly.Events.ImpressionAsync(shown);
await likyly.Events.ClickAsync(shown);
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

```csharp
// Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
var front = new LikylyClient(new LikylyClientOptions
{
    ApiKey = Environment.GetEnvironmentVariable("LIKYLY_PUBLIC_KEY")!,
});

// A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
await front.Events.ViewAsync(new EventInput { SessionId = "sess_abc", ItemId = "SKU-2" });
var forVisitor = await front.Recommendations.GetAsync(new RecommendationRequest { SessionId = "sess_abc", ViewedItemIds = ["SKU-2"], Placement = "product_page", Limit = 3 });
```

## Events

`track` is the one mechanism; `impression`, `view`, `click`, `add_to_cart`, `remove_from_cart` and `purchase` are shortcuts that call it with the matching type. Event types are **open strings**: use your own (`favorite`, `share`, `video_played`, ...) as freely as the official ones.

```csharp
// Any string is a valid event type: track what matters to your business.
await likyly.Events.TrackAsync("favorite", new EventInput { UserId = "user_123", ItemId = "SKU-2", Properties = new Dictionary<string, object?> { ["list"] = "wishlist" } });

// Up to 1000 events per call, each with its own type.
await likyly.Events.TrackManyAsync(
[
    new TypedEventInput { Type = "view", UserId = "user_123", ItemId = "SKU-3" },
    new TypedEventInput { Type = "add_to_cart", UserId = "user_123", ItemId = "SKU-3", Quantity = 1 },
]);
```

- An event needs an item and a user, a session, or both (both ties the anonymous session to the user once they log in).
- Anonymous visitors: generate a session id (any string) and keep it in a cookie; no login needed.
- `properties` is a free-form object (price, currency, order id, ...). Its keys are stored exactly as you send them and are never renamed by the SDK.
- `event_id` (idempotency key): replaying an event with the same id records nothing and returns `duplicate: true`. Set it on purchases, for example `purchase_<orderId>_<itemId>`.
- `placement` is a free-form label of where something happened or will be displayed (`homepage`, `product_page`, `cart`, ...).

## Advanced recommendations

`recommendations.get` is the normal way. When you want to pick the strategy yourself:

```csharp
// Advanced Recommendations: one strategy at a time, when you want to choose.
var similar = await likyly.Recommendations.SimilarAsync(new SimilarOptions { ItemId = "SKU-1", Limit = 3 });
var hybrid = await likyly.Recommendations.HybridAsync(new HybridOptions { UserId = "user_123", ItemId = "SKU-1", Alpha = 0.7, Limit = 3 });
var session = await likyly.Recommendations.SessionAsync(new SessionOptions { ViewedItemIds = ["SKU-1", "SKU-2"], Limit = 3 });
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
| `RequestTimeoutException` | The request timed out |

Errors that come from the API carry `StatusCode`, `RequestId`, `RetryAfter` (seconds), `Body`. Quote the request id when you contact support.

```csharp
try
{
    await likyly.Items.GetAsync("does-not-exist");
}
catch (NotFoundException e)
{
    Console.WriteLine($"no such item, request {e.RequestId}");
}
catch (RateLimitException e)
{
    Console.WriteLine($"slow down, retry in {e.RetryAfter}");
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
| API key | `ApiKey` | required |
| Base URL | `BaseUrl` | `https://api.likyly.com` |
| Catalog | `Catalog` | your only catalog |
| Timeout | `Timeout` (`TimeSpan`) | 10 s |
| Retries | `MaxRetries` | 2 |
| User-Agent suffix | `UserAgent` | none |
| Custom HTTP | `new LikylyClient(options, httpClient)` | a new `HttpClient` |

```csharp
var tuned = new LikylyClient(new LikylyClientOptions
{
    ApiKey = Environment.GetEnvironmentVariable("LIKYLY_SECRET_KEY")!,
    Timeout = TimeSpan.FromSeconds(5), // per attempt (default 10 s)
    MaxRetries = 3, // automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
    UserAgent = "my-shop/1.4", // appended to the SDK's User-Agent
});
```

Pass a `CancellationToken` as the last argument; the client's `Timeout` applies per attempt. Register `LikylyClient` as a singleton (`services.AddSingleton(...)`).

## API reference

`key` says which API key may call it: **S** = secret key only, **P** = public or secret key.

| Method | What it does | Key |
|---|---|---|
| `likyly.Items.GetAsync` | One item by your own id | S |
| `likyly.Items.ListAsync` | One page of the catalog (`limit` / `offset`), with the catalog's total | S |
| `likyly.Items.UpsertAsync` | Create or replace an item (idempotent) | S |
| `likyly.Items.DeleteAsync` | Remove an item (events already recorded for it are kept) | S |
| `likyly.Items.UpsertManyAsync` | Batch upsert, 1 to 1000 items | S |
| `likyly.Items.ImportAsync` | Alias of the batch upsert above (JSON batch) | S |
| `likyly.Items.DeleteManyAsync` | Batch delete, 1 to 1000 ids; unknown ids are reported, not raised | S |
| `likyly.Users.GetAsync` | One user profile | S |
| `likyly.Users.ListAsync` | One page of users (`limit` / `offset`) | S |
| `likyly.Users.UpsertAsync` | Create or replace a profile (free-form `properties`) | S |
| `likyly.Users.DeleteAsync` | Erase the profile **and every event recorded for that user** | S |
| `likyly.Users.ImportAsync` | Batch upsert of users, 1 to 1000 | S |
| `likyly.Events.TrackAsync` | Record an event of any type (open string) | P |
| `likyly.Events.TrackManyAsync` | Up to 1000 events in one call, each with its own type | P |
| `likyly.Events.ImpressionAsync` | The item was displayed to the visitor | P |
| `likyly.Events.ViewAsync` | The visitor looked at the item | P |
| `likyly.Events.ClickAsync` | The visitor clicked the item | P |
| `likyly.Events.AddToCartAsync` | The visitor added the item to their cart | P |
| `likyly.Events.RemoveFromCartAsync` | The visitor removed the item from their cart | P |
| `likyly.Events.PurchaseAsync` | The visitor bought the item (set an event id) | P |
| `likyly.Recommendations.GetAsync` | Recommendations with the automatic strategy: user, session, item or viewed items | P |
| `likyly.Recommendations.PopularAsync` | Advanced: the most popular items | P |
| `likyly.Recommendations.SimilarAsync` | Advanced: items similar to one item | P |
| `likyly.Recommendations.CollaborativeAsync` | Advanced: what similar users liked (needs a trained model) | P |
| `likyly.Recommendations.HybridAsync` | Advanced: similar items personalised for a user (`alpha`) | P |
| `likyly.Recommendations.SessionAsync` | Advanced: from viewed items or a user's view history | P |

Lists are paginated with `limit` and `offset` (the API's default page is 100 items); the total is reported next to the page. Users have a batch `import` but no batch delete: the API has no such endpoint. `import` on items is an alias of the batch upsert (the CSV import is a dashboard feature).

## Notes for .NET

- Request and response models are C# records/classes with nullable annotations; identifiers are strings and are never coerced. The keys inside `Properties` (a `Dictionary<string, object?>`) are yours and are never renamed.

## Documentation and support

- Documentation: <https://likyly.com/docs/sdk/dotnet>
- REST API reference (OpenAPI): <https://api.likyly.com/docs>
- Package: Likyly on NuGet

MIT licensed.
