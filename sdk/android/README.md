# LIKYLY for Android

The official Android / Kotlin client for LIKYLY: show recommendations in your app and record what visitors
do, so LIKYLY learns from it.

- Pure Kotlin, Java 8 bytecode: works in any Android app (minSdk 21+) and on any JVM. No Android Gradle Plugin
  needed to build it.
- One dependency, `kotlinx-coroutines-core`, which your app almost certainly has already. No JSON library, no
  HTTP library: it uses `java.net.HttpURLConnection` (swap in OkHttp with your own `LikylyTransport`).
- `suspend` functions: call them from `viewModelScope`, `lifecycleScope` or any coroutine.

## Install

```kotlin
// app/build.gradle.kts
dependencies {
    implementation("com.likyly:likyly-android:1.0.0")
}
```

Your app needs the `INTERNET` permission (`<uses-permission android:name="android.permission.INTERNET" />`).

> Until the package is published, build it from this folder (`./gradlew jar`, then
> `implementation(files("libs/likyly-android-1.0.0.jar"))` plus `kotlinx-coroutines-core`), or
> `./gradlew publishToMavenLocal` and add `mavenLocal()` to your repositories.

## Use the public key - never the secret key

An app runs on the visitor's phone, and anyone can extract a key from an APK. Use the **public key**
(`pk_...`, account page, "Clé publique"): it can only ask for recommendations and record events. The
constructor refuses a secret (`sk_`) or developer (`lk_`) key with a `ValidationException`, so the mistake
fails at once instead of shipping. Your catalog (items, users) is sent from your backend, with the secret key
and a server SDK.

## Quick start

Build one client and one session for the whole app:

```kotlin
class ShopApp : Application() {
    lateinit var likyly: Likyly
    lateinit var session: LikylySession

    override fun onCreate() {
        super.onCreate()
        likyly = Likyly(BuildConfig.LIKYLY_PUBLIC_KEY)
        // An anonymous visitor id, created once and kept in the app's private files.
        session = LikylySession(FileAnonymousIdStore(filesDir))
    }
}
```

Ask a placement what to show, then report what was shown and tapped, with the `recommendationId` it came with:

```kotlin
viewModelScope.launch {
    val result = try {
        likyly.recommend(
            PlacementRequest(
                placement = "pdp-related",                       // configured once in LIKYLY
                context = PlacementContext(itemId = productId, userId = userId, sessionId = session.anonymousId),
                limit = 4,
            ),
        )
    } catch (e: LikylyException) {
        null // show your own content instead (see "When LIKYLY is unavailable")
    }
    _items.value = result?.items.orEmpty()
}

// A card became visible (once per card):
likyly.events.impression(EventInput(itemId = item.itemId, sessionId = session.anonymousId,
    recommendationId = result.recommendationId, placement = "pdp-related"))

// A card was tapped:
likyly.events.click(EventInput(itemId = item.itemId, sessionId = session.anonymousId,
    recommendationId = result.recommendationId, placement = "pdp-related"))
```

Record what visitors do anywhere else too:

```kotlin
likyly.events.view(EventInput(itemId = "SKU-456", userId = userId, sessionId = session.anonymousId))
likyly.events.addToCart(EventInput(itemId = "SKU-456", sessionId = session.anonymousId, quantity = 1))
likyly.events.purchase(EventInput(itemId = "SKU-456", userId = userId, quantity = 1,
    eventId = "purchase_${orderId}_SKU-456"))           // an eventId makes retries safe
likyly.events.track("favorite", EventInput(itemId = "SKU-456", userId = userId)) // your own event types
```

When the visitor signs in, attach their anonymous history to their account (once; calling it again is harmless):

```kotlin
session.identify(likyly, userId)
```

A complete ViewModel is in [examples/RecommendationsViewModel.kt](examples/RecommendationsViewModel.kt).

## When LIKYLY is unavailable

Placement answers are cached in memory for one hour (same placement, limit and context), so going back to a
screen costs nothing. After a network error, a timeout, a 429 or a 5xx, placement calls throw `PausedException`
at once for 30 seconds, so the screen shows its own content instead of waiting. Tune or disable both:

```kotlin
Likyly(key, LikylyOptions(cache = RecommendCacheOptions(ttlSeconds = 600, pauseSeconds = 15)))
Likyly(key, LikylyOptions(cache = null))   // no cache, no pause
likyly.clearRecommendationCache()          // forget the cached answers
```

## Configuration

| Option | `LikylyOptions` field | Default |
|---|---|---|
| Base URL | `baseUrl` | `https://api.likyly.com` |
| Catalog | `catalog` | your only catalog |
| Timeout | `timeoutMillis` (per attempt) | 10 000 |
| Retries | `maxRetries` | 2 |
| User-Agent suffix | `userAgent` | none |
| Recommendation cache | `cache` | 1 hour; 30 s pause after a failure |
| HTTP stack | `transport` (`LikylyTransport`) | `HttpURLConnection` |

Retries: 429, 502, 503, 504 and dropped connections are retried with backoff (a `Retry-After` up to 60 s is
honoured). An event without `eventId` is never retried after an ambiguous failure, so it is never recorded twice.

## Errors

Every exception is a `LikylyException`; coroutine cancellation is never wrapped.

| Exception | When |
|---|---|
| `ValidationException` | invalid call, caught before sending, or a 422 (`statusCode`, `requestId`) |
| `NetworkException` | no HTTP response (offline, DNS, connection reset) |
| `TimeoutException` | the request timed out |
| `PausedException` | LIKYLY failed moments ago; `retryInSeconds` |
| `ApiException` | any other error status: `statusCode`, `requestId`, `retryAfterSeconds`, `body` |
| `AuthenticationException` | 401 - missing, invalid or revoked key |
| `PermissionDeniedException` | 403 - e.g. an operation the public key cannot do |
| `NotFoundException` | 404 - e.g. an unknown placement |
| `RateLimitException` | 429 - see `retryAfterSeconds` |

## Methods

| Operation | Method |
|---|---|
| placements.recommend | `likyly.recommend(...)` / `likyly.placements.recommend(...)` |
| events | `track`, `trackMany`, `impression`, `view`, `click`, `addToCart`, `removeFromCart`, `purchase`, `identify` |
| recommendations (advanced) | `get`, `popular`, `similar`, `collaborative`, `hybrid`, `session` |

`items` and `users` are not in this SDK: they need the secret key, which never belongs in an app.

## Java

The API is `suspend`-only. From Java, wrap calls in a small Kotlin helper (`CoroutineScope.future { ... }`
from `kotlinx-coroutines-jdk8`, or a callback) in your app.

## ProGuard / R8

No rules needed: the SDK uses no reflection.

## Development

```sh
./gradlew test    # conformance scenarios (../conformance/scenarios.json) + behaviour tests
./gradlew jar     # build/libs/likyly-android-1.0.0.jar
```

Against a real API (a throwaway one in Docker): `../conformance/local-api.sh start`, then
`set -a; source ../conformance/.local-api.env; set +a; ./gradlew test` also runs `LiveTest` - a whole app
session (views, placement, cache, impression and click, identify, idempotent purchase, batch, errors).
