# LIKYLY SDK for Go

The official Go client for the [LIKYLY](https://likyly.com) recommendations API. Send your catalog and what your visitors do, get personalised recommendations back.

```
catalog (items)  →  events  →  recommendations  →  impression → click → purchase
```

## Install

```sh
go get github.com/likyly/likyly-go
```

Go 1.21 or later. Standard library only. Every call takes a `context.Context`. Safe for concurrent use.

## Quick start

The snippets run inside `func main()`; `log.Fatal` keeps them short, handle errors as you see fit.

### Initialize

Create one client and reuse it. Use your **secret** key on your server (see [Which key?](#which-key)).

```go
import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/likyly/likyly-go"
)

ctx := context.Background()

// Backend only: the secret key gives access to your catalog and your users.
client, err := likyly.New(os.Getenv("LIKYLY_SECRET_KEY"))
if err != nil {
	log.Fatal(err)
}
```

### Send your catalog

```go
// Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
_, err = client.Items.UpsertMany(ctx, []likyly.ItemImport{
	{ItemID: "SKU-1", Title: "Nike Air Max", Description: "Running shoe with air cushioning", Properties: likyly.Properties{"category": "shoes", "brand": "Nike", "price": 129.9}},
	{ItemID: "SKU-2", Title: "Adidas Ultraboost", Description: "Responsive running shoe", Properties: likyly.Properties{"category": "shoes", "brand": "Adidas", "price": 149}},
	{ItemID: "SKU-3", Title: "Nike Pegasus", Description: "Everyday running shoe", Properties: likyly.Properties{"category": "shoes", "brand": "Nike", "price": 119}},
})
if err != nil {
	log.Fatal(err)
}

// Users are optional: describe them if you want the profile to travel with their events.
_, err = client.Users.Upsert(ctx, "user_123", likyly.UserInput{Properties: likyly.Properties{"country": "FR", "segment": "premium"}})
if err != nil {
	log.Fatal(err)
}
```

Ids are your own: any string (`SKU-123`, a UUID, `gid://shopify/Product/123`). `properties` is free-form; `category` and `description` improve similarity, everything else is stored as is.

### Track events

```go
// Tell LIKYLY what your visitors do.
if _, err = client.Events.View(ctx, likyly.EventInput{UserID: "user_123", ItemID: "SKU-1"}); err != nil {
	log.Fatal(err)
}

// Purchases carry an EventID: replaying the call can never count the sale twice.
_, err = client.Events.Purchase(ctx, likyly.EventInput{
	EventID:    "purchase_order_9281_SKU-1",
	UserID:     "user_123",
	ItemID:     "SKU-1",
	Quantity:   1,
	Properties: likyly.Properties{"price": 129.9, "currency": "EUR", "orderId": "order_9281"},
})
if err != nil {
	log.Fatal(err)
}
```

### Get recommendations

```go
// Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
recs, err := client.Recommendations.Get(ctx, likyly.RecommendationRequest{UserID: "user_123", Placement: "homepage", Limit: 3})
if err != nil {
	log.Fatal(err)
}

fmt.Println("strategy:", recs.Strategy)
for _, item := range recs.Items {
	fmt.Println(item.ItemID, item.Title)
}
```

You never choose an algorithm: LIKYLY picks the best strategy for what it knows about the visitor and tells you which one it used (`strategy`: `hybrid`, `content`, `collaborative`, `session` or `popular`). With no user, no session and no item you still get a useful answer: what is popular.

### Close the loop

```go
// Send the RecommendationID back: LIKYLY measures which recommendations get seen, clicked and bought.
shown := likyly.EventInput{UserID: "user_123", ItemID: recs.Items[0].ItemID, RecommendationID: recs.RecommendationID, Placement: "homepage"}
if _, err = client.Events.Impression(ctx, shown); err != nil {
	log.Fatal(err)
}
if _, err = client.Events.Click(ctx, shown); err != nil {
	log.Fatal(err)
}
```

Send the recommendation id back on the events that follow (`impression` when it is displayed, `click`, `add_to_cart`, `purchase`) and LIKYLY can measure which recommendations actually convert.

## Which key?

| | Secret key | Public key |
|---|---|---|
| Where | your server only | anywhere, including a web page |
| Recommendations | yes | yes |
| Record events | yes | yes |
| Items and users (create, read, delete) | yes | no (`*PermissionDeniedError`) |
| `debug` explanations | yes | no |

The public key can only read recommendations and write events, so it is safe to expose. **Never put the secret key in code that ships to a browser or a mobile app.**

```go
// Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
front, err := likyly.New(os.Getenv("LIKYLY_PUBLIC_KEY"))
if err != nil {
	log.Fatal(err)
}

// A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
if _, err = front.Events.View(ctx, likyly.EventInput{SessionID: "sess_abc", ItemID: "SKU-2"}); err != nil {
	log.Fatal(err)
}
forVisitor, err := front.Recommendations.Get(ctx, likyly.RecommendationRequest{SessionID: "sess_abc", ViewedItemIDs: []string{"SKU-2"}, Placement: "product_page", Limit: 3})
if err != nil {
	log.Fatal(err)
}
```

## Events

`track` is the one mechanism; `impression`, `view`, `click`, `add_to_cart`, `remove_from_cart` and `purchase` are shortcuts that call it with the matching type. Event types are **open strings**: use your own (`favorite`, `share`, `video_played`, ...) as freely as the official ones.

```go
// Any string is a valid event type: track what matters to your business.
_, err = client.Events.Track(ctx, "favorite", likyly.EventInput{UserID: "user_123", ItemID: "SKU-2", Properties: likyly.Properties{"list": "wishlist"}})
if err != nil {
	log.Fatal(err)
}

// Up to 1000 events per call, each with its own type.
_, err = client.Events.TrackMany(ctx, []likyly.TypedEvent{
	{Type: "view", EventInput: likyly.EventInput{UserID: "user_123", ItemID: "SKU-3"}},
	{Type: "add_to_cart", EventInput: likyly.EventInput{UserID: "user_123", ItemID: "SKU-3", Quantity: 1}},
})
if err != nil {
	log.Fatal(err)
}
```

- An event needs an item and a user, a session, or both (both ties the anonymous session to the user once they log in).
- Anonymous visitors: generate a session id (any string) and keep it in a cookie; no login needed.
- `properties` is a free-form object (price, currency, order id, ...). Its keys are stored exactly as you send them and are never renamed by the SDK.
- `event_id` (idempotency key): replaying an event with the same id records nothing and returns `duplicate: true`. Set it on purchases, for example `purchase_<orderId>_<itemId>`.
- `placement` is a free-form label of where something happened or will be displayed (`homepage`, `product_page`, `cart`, ...).

## Advanced recommendations

`recommendations.get` is the normal way. When you want to pick the strategy yourself:

```go
// Advanced Recommendations: one strategy at a time, when you want to choose.
alpha := 0.7
similar, err := client.Recommendations.Similar(ctx, likyly.SimilarOptions{ItemID: "SKU-1", AdvancedOptions: likyly.AdvancedOptions{Limit: 3}})
if err != nil {
	log.Fatal(err)
}
hybrid, err := client.Recommendations.Hybrid(ctx, likyly.HybridOptions{UserID: "user_123", ItemID: "SKU-1", Alpha: &alpha, AdvancedOptions: likyly.AdvancedOptions{Limit: 3}})
if err != nil {
	log.Fatal(err)
}
session, err := client.Recommendations.Session(ctx, likyly.SessionOptions{ViewedItemIDs: []string{"SKU-1", "SKU-2"}, AdvancedOptions: likyly.AdvancedOptions{Limit: 3}})
if err != nil {
	log.Fatal(err)
}
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

Errors are typed (every one is a `error`). Catch the specific ones you care about:

| Error | When |
|---|---|
| `*ValidationError` | The request is invalid: caught by the SDK before anything is sent, or a 422 from the API (the message names the offending fields) |
| `*AuthenticationError` | 401: missing, invalid or revoked API key |
| `*PermissionDeniedError` | 403: for example a public key used for a secret-key operation, or a plan limit |
| `*NotFoundError` | 404 |
| `*RateLimitError` | 429: see the retry-after value |
| `*APIError` | Any other error status from the API (the four rows above are its subtypes) |
| `*NetworkError` | No HTTP response: DNS, connection reset, TLS |
| `*TimeoutError` | The request timed out |

Errors that come from the API carry `StatusCode`, `RequestID`, `RetryAfter` (`time.Duration`), `Body`. Quote the request id when you contact support.

```go
_, err = client.Items.Get(ctx, "does-not-exist")
var notFound *likyly.NotFoundError
var rateLimited *likyly.RateLimitError
switch {
case errors.As(err, &notFound):
	fmt.Println("no such item, request", notFound.RequestID)
case errors.As(err, &rateLimited):
	fmt.Println("slow down, retry in", rateLimited.RetryAfter)
case err != nil:
	log.Fatal(err)
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
| API key | first argument of `New` | required |
| Base URL | `WithBaseURL` | `https://api.likyly.com` |
| Catalog | `WithCatalog` | your only catalog |
| Timeout | `WithTimeout` (`time.Duration`) | 10 s |
| Retries | `WithMaxRetries` | 2 |
| User-Agent suffix | `WithUserAgent` | none |
| Custom HTTP | `WithHTTPClient` (any `Doer`, e.g. `*http.Client`) | `http.DefaultClient` |

```go
tuned, err := likyly.New(os.Getenv("LIKYLY_SECRET_KEY"),
	likyly.WithTimeout(5*time.Second),                                   // per attempt (default 10s)
	likyly.WithMaxRetries(3),                                            // automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
	likyly.WithUserAgent("my-shop/1.4"),                                 // appended to the SDK's User-Agent
)
if err != nil {
	log.Fatal(err)
}
```

Cancel or set a deadline through the `context.Context`. Per-call options go last: `WithRequestTimeout(d)`, `WithRequestMaxRetries(n)`.

## API reference

`key` says which API key may call it: **S** = secret key only, **P** = public or secret key.

| Method | What it does | Key |
|---|---|---|
| `client.Items.Get` | One item by your own id | S |
| `client.Items.List` | One page of the catalog (`limit` / `offset`), with the catalog's total | S |
| `client.Items.Upsert` | Create or replace an item (idempotent) | S |
| `client.Items.Delete` | Remove an item (events already recorded for it are kept) | S |
| `client.Items.UpsertMany` | Batch upsert, 1 to 1000 items | S |
| `client.Items.Import` | Alias of the batch upsert above (JSON batch) | S |
| `client.Items.DeleteMany` | Batch delete, 1 to 1000 ids; unknown ids are reported, not raised | S |
| `client.Users.Get` | One user profile | S |
| `client.Users.List` | One page of users (`limit` / `offset`) | S |
| `client.Users.Upsert` | Create or replace a profile (free-form `properties`) | S |
| `client.Users.Delete` | Erase the profile **and every event recorded for that user** | S |
| `client.Users.Import` | Batch upsert of users, 1 to 1000 | S |
| `client.Events.Track` | Record an event of any type (open string) | P |
| `client.Events.TrackMany` | Up to 1000 events in one call, each with its own type | P |
| `client.Events.Impression` | The item was displayed to the visitor | P |
| `client.Events.View` | The visitor looked at the item | P |
| `client.Events.Click` | The visitor clicked the item | P |
| `client.Events.AddToCart` | The visitor added the item to their cart | P |
| `client.Events.RemoveFromCart` | The visitor removed the item from their cart | P |
| `client.Events.Purchase` | The visitor bought the item (set an event id) | P |
| `client.Recommendations.Get` | Recommendations with the automatic strategy: user, session, item or viewed items | P |
| `client.Recommendations.Popular` | Advanced: the most popular items | P |
| `client.Recommendations.Similar` | Advanced: items similar to one item | P |
| `client.Recommendations.Collaborative` | Advanced: what similar users liked (needs a trained model) | P |
| `client.Recommendations.Hybrid` | Advanced: similar items personalised for a user (`alpha`) | P |
| `client.Recommendations.Session` | Advanced: from viewed items or a user's view history | P |

Lists are paginated with `limit` and `offset` (the API's default page is 100 items); the total is reported next to the page. Users have a batch `import` but no batch delete: the API has no such endpoint. `import` on items is an alias of the batch upsert (the CSV import is a dashboard feature).

## Notes for Go

- Optional fields use zero values as "not set" (`Limit: 0` = the API's default). `HybridOptions.Alpha` is a `*float64` so that `0` is expressible. Test errors with `errors.As`; a `*ValidationError` from a 422 also matches `*APIError`.
- Request and response fields are `PascalCase` structs; the keys inside `Properties` (a `map[string]any`) are yours and are never renamed.

## Documentation and support

- Documentation: <https://likyly.com/docs/sdk/go>
- REST API reference (OpenAPI): <https://api.likyly.com/docs>
- Package: github.com/likyly/likyly-go on Go modules

MIT licensed.
