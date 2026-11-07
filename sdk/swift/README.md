# LIKYLY for iOS (Swift)

The LIKYLY SDK for apps on Apple platforms: iOS / iPadOS 15+, macOS 12+, tvOS 15+, watchOS 8+.
Swift 5.9+ (Xcode 15+), `async`/`await`, no dependency.

It runs on the visitor's device, so it covers what the **public key** allows:

| | |
|---|---|
| `likyly.placements` / `likyly.recommend(...)` | what to show, where (recommended) |
| `likyly.events` | what visitors do: `view`, `impression`, `click`, `addToCart`, `removeFromCart`, `purchase`, `track`, `trackMany`, `identify` |
| `likyly.recommendations` | what to show, strategy by strategy (advanced): `get`, `popular`, `similar`, `collaborative`, `hybrid`, `session` |
| `LikylySession` | an anonymous visitor id kept on the device, and `identify` at sign-in |

Your catalog (`items`, `users`) is synced from your backend with a server SDK and the secret key -
never from the app.

## Install

Swift Package Manager: in Xcode, *File › Add Package Dependencies…*, or in `Package.swift`:

```swift
.package(url: "https://github.com/likyly/likyly-swift", from: "1.0.0")
// target: .product(name: "Likyly", package: "likyly-swift")
```

## Quick start

```swift
import Likyly

let likyly = try Likyly(apiKey: "pk_live_xxx")   // the PUBLIC key
let session = LikylySession()                     // anonymous id, kept in UserDefaults

// What to show on a product screen (placement configured once in the LIKYLY console)
let recs = try await likyly.recommend(
    "pdp-related",
    context: PlacementContext(itemId: "SKU-456", userId: currentUser?.id, sessionId: session.anonymousId),
    limit: 8)

// What the visitor does - send recommendationId back to attribute it
try await likyly.events.impression(EventInput(itemId: recs.items[0].itemId, sessionId: session.anonymousId,
                                              recommendationId: recs.recommendationId, placement: recs.placement))
try await likyly.events.view(EventInput(itemId: "SKU-456", sessionId: session.anonymousId))

// At sign-in: the anonymous history now counts for the account
try await session.identify(likyly, userId: user.id)

// A purchase: set eventId so a retry can never count it twice
try await likyly.events.purchase(EventInput(itemId: "SKU-456", userId: user.id, eventId: "purchase_\(order.id)_SKU-456",
                                            quantity: 1, properties: ["price": 129.9, "currency": "EUR"]))
```

A complete SwiftUI screen (fetch, impressions on appear, clicks, fallback): [examples/RelatedProductsView.swift](examples/RelatedProductsView.swift).

## Configuration

```swift
let likyly = try Likyly(apiKey: "pk_live_xxx", options: LikylyOptions(
    catalog: "films",                                    // if your account has several catalogs
    timeout: 5,                                          // seconds, per attempt (default 10)
    maxRetries: 2,                                       // 429, 502, 503, 504, dropped connections
    userAgent: "my-shop-ios/4.2",
    cache: RecommendCacheOptions(ttlSeconds: 3600, pauseSeconds: 30)  // or nil
))
```

Every method also takes `options: RequestOptions(timeout:maxRetries:)` for one call, and honours Task
cancellation (a cancelled `.task` throws `CancellationError`).

## Behaviour you can rely on

- **Placement answers are cached** in memory for an hour per placement, context and limit (who the
  anonymous visitor is does not count), and keep their `recommendationId`, so attribution still works.
  `await likyly.clearRecommendationCache()` forgets them.
- **When LIKYLY is down** (offline, timeout, 429, 5xx) placement calls throw `LikylyError.paused` at once
  for 30 s: show your own fallback instead of a spinner.
- **Retries**: reads are retried with exponential backoff and jitter; an event is only retried when it
  carries an `eventId` (a 429 is always retried, nothing was recorded).
- **Ids** are your own strings, sent as is (`gid://shopify/Product/9` is fine).

## Errors

Everything thrown is a `LikylyError`:

```swift
do {
    recs = try await likyly.recommend("home-personalized", context: ctx)
} catch LikylyError.paused {
    showFallback()
} catch let error as LikylyError {
    print(error.localizedDescription, error.statusCode ?? 0, error.requestId ?? "")
}
```

`validation` (before sending, or a 422) · `network` · `timeout` · `paused` · `authentication` (401) ·
`permissionDenied` (403) · `notFound` (404) · `rateLimit` (429, `retryAfter`) · `api` (other statuses).

## Security and privacy

- The constructor **refuses a secret (`sk_`) or developer (`lk_`) key**: anything in an app binary can be
  extracted. Use the public key (`pk_`, account page's "Clé publique").
- `LikylySession` is a random id stored in your app's `UserDefaults`, not the IDFA, never shared with other
  apps: no App Tracking Transparency prompt is needed. Declare *Product Interaction* data in your App
  Store privacy label. `session.reset()` at sign-out.

## Tests

```sh
swift test
```

The suite replays the request/response scenarios shared by every LIKYLY SDK
(`conformance/scenarios.json`) for the public-key operations, plus the retry, cache, pause,
encoding and session rules.

`LiveTests` plays a whole app session against a real API (views, placement, cache, impression and
click, identify, idempotent purchase, batch, errors) when `LIKYLY_TEST_URL`, `LIKYLY_TEST_SECRET_KEY`
and `LIKYLY_TEST_PUBLIC_KEY` are set; it is skipped otherwise.
