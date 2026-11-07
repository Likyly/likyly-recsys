package com.likyly

import kotlinx.coroutines.delay

/** The SDK version, sent in the User-Agent. */
public const val LIKYLY_VERSION: String = "1.0.0"

/** The LIKYLY API. */
public const val DEFAULT_BASE_URL: String = "https://api.likyly.com"

public data class LikylyOptions(
    /** Defaults to https://api.likyly.com. */
    val baseUrl: String = DEFAULT_BASE_URL,
    /** Which of your catalogs to use, if your account has several. Omit it if you have one. */
    val catalog: String? = null,
    /** Per-attempt request timeout. */
    val timeoutMillis: Long = 10_000,
    /** Automatic retries on transient failures (429, 502, 503, 504, dropped connections). 0 disables them. */
    val maxRetries: Int = 2,
    /** Appended to the SDK's User-Agent, e.g. "my-shop-android/4.2". */
    val userAgent: String? = null,
    /** Placement recommendation cache and pause after a failure; null turns both off. */
    val cache: RecommendCacheOptions? = RecommendCacheOptions(),
    /** The HTTP stack. Defaults to java.net.HttpURLConnection. */
    val transport: LikylyTransport = UrlConnectionTransport,
)

/**
 * The LIKYLY client for Android apps. Build it once (e.g. in your Application class) with your **public** key
 * ("pk_..."), and share it: it is safe for concurrent use.
 *
 *     likyly.events           what visitors do
 *     likyly.placements       what to show them, where (recommended)
 *     likyly.recommendations  what to show them, strategy by strategy (advanced)
 *
 * The catalog itself (items, users) is managed from your backend with the secret key and a server SDK -
 * never from an app, whose code anyone can read.
 */
public class Likyly internal constructor(
    apiKey: String,
    options: LikylyOptions,
    sleep: suspend (Long) -> Unit,
    random: () -> Double,
    now: () -> Long,
) {
    public constructor(apiKey: String, options: LikylyOptions = LikylyOptions()) :
        this(apiKey, options, { delay(it) }, { Math.random() }, { System.currentTimeMillis() })

    public val events: Events
    public val placements: Placements
    public val recommendations: Recommendations

    init {
        if (apiKey.isBlank()) throw ValidationException("apiKey is required (create one in your LIKYLY account)")
        if (UNSAFE_PREFIXES.any { apiKey.startsWith(it) }) {
            throw ValidationException(
                "This looks like a secret or developer key, and this code runs in an app on the visitor's device. " +
                    "Anyone can extract a key from an app - use the restricted public key here instead (\"pk_...\", " +
                    "account page's \"Clé publique\"), and keep the secret/developer key server-side only.",
            )
        }
        val ua = "likyly-android/$LIKYLY_VERSION" + (options.userAgent?.takeIf { it.isNotBlank() }?.let { " $it" } ?: "")
        val core = Core(apiKey, options.baseUrl, options.catalog, options.timeoutMillis, options.maxRetries, ua, options.transport, sleep, random)
        events = Events(core)
        placements = Placements(core, options.cache?.let { RecommendCache(it, now) })
        recommendations = Recommendations(core)
    }

    /** Shorthand for `likyly.placements.recommend(...)`. */
    public suspend fun recommend(request: PlacementRequest): PlacementResponse = placements.recommend(request)

    /** Forgets every cached placement answer and ends a pause after a failure. */
    public fun clearRecommendationCache() {
        placements.clearCache()
    }

    private companion object {
        /** "sk_" secret, "lk_" developer ("pk_" is public; older, unprefixed keys keep working). */
        val UNSAFE_PREFIXES = listOf("sk_", "lk_")
    }
}
