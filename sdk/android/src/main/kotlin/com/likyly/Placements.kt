package com.likyly

/**
 * Placement recommendations are cached in memory for [ttlSeconds] (same placement, limit and context; who the
 * anonymous visitor is does not count), and after LIKYLY fails (network error, timeout, 429, 5xx) placement calls
 * throw [PausedException] at once for [pauseSeconds], so the screen shows its own fallback instead of waiting.
 */
public data class RecommendCacheOptions(
    /** How long an answer is reused. 0 disables the cache. */
    val ttlSeconds: Long = 3600,
    /** After LIKYLY fails, how long to skip calls. 0 always calls. */
    val pauseSeconds: Long = 30,
)

internal class RecommendCache(options: RecommendCacheOptions, private val now: () -> Long) {
    private val ttlMs = maxOf(0L, options.ttlSeconds) * 1000
    private val pauseMs = maxOf(0L, options.pauseSeconds) * 1000
    private val entries = object : LinkedHashMap<String, Pair<Long, PlacementResponse>>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Pair<Long, PlacementResponse>>?): Boolean =
            size > MAX_ENTRIES
    }
    @Volatile private var pausedUntil = 0L

    @Synchronized
    fun get(key: String): PlacementResponse? {
        if (ttlMs == 0L) return null
        val (expires, value) = entries[key] ?: return null
        if (expires <= now()) {
            entries.remove(key)
            return null
        }
        return value
    }

    @Synchronized
    fun set(key: String, value: PlacementResponse) {
        if (ttlMs == 0L) return
        entries[key] = (now() + ttlMs) to value
    }

    @Synchronized
    fun clear() {
        entries.clear()
        pausedUntil = 0L
    }

    fun isPaused(): Boolean = now() < pausedUntil

    fun pauseRemainingSeconds(): Long = maxOf(0L, (pausedUntil - now() + 999) / 1000)

    fun recordFailure() {
        if (pauseMs > 0) pausedUntil = now() + pauseMs
    }

    fun recordSuccess() {
        pausedUntil = 0L
    }

    companion object {
        const val MAX_ENTRIES = 500
    }
}

/**
 * `likyly.placements` - the recommended way to ask for recommendations. A placement ("what to recommend, where,
 * with what fallback and filters") is configured once in LIKYLY (dashboard or MCP admin tools, never from this
 * SDK) and called by its slug from here on.
 */
public class Placements internal constructor(private val core: Core, internal val cache: RecommendCache?) {

    /**
     * Recommendations for one placement: send whatever context you have (itemId, userId, sessionId, ...) - the
     * placement's own configuration decides the strategy, fallback and filters. Send recommendationId back on
     * the events that follow to attribute them.
     */
    public suspend fun recommend(request: PlacementRequest): PlacementResponse {
        val placement = requireId(request.placement, "placement")
        if (request.limit != null && request.limit <= 0) throw ValidationException("limit must be a positive integer")
        val body = LinkedHashMap<String, Any?>()
        request.context?.let { body["context"] = contextBody(it) }
        request.limit?.let { body["limit"] = it }

        val key = if (cache != null) cacheKey(placement, request) else ""
        cache?.get(key)?.let { return it }
        if (cache != null && cache.isPaused()) throw PausedException(cache.pauseRemainingSeconds())
        try {
            // A read in effect: each call mints a fresh recommendation id, so repeating it is harmless - it is retried.
            val w = core.request(Call("POST", "/placements/${encodeSegment(placement)}/recommend", body = body, idempotent = true))
            val result = PlacementResponse(
                recommendationId = w.string("recommendation_id") ?: "",
                placement = w.string("placement") ?: placement,
                strategyUsed = w.string("strategy_used") ?: "",
                items = recommendedItems(w.list("items")),
            )
            cache?.recordSuccess()
            cache?.set(key, result)
            return result
        } catch (e: LikylyException) {
            if (isOutage(e)) cache?.recordFailure()
            throw e
        }
    }

    /** Forgets every cached answer (e.g. after "Vider le cache" in the LIKYLY console) and ends a pause. */
    public fun clearCache() {
        cache?.clear()
    }

    private fun contextBody(c: PlacementContext): Map<String, Any?> {
        val body = LinkedHashMap<String, Any?>()
        fun put(key: String, value: String?) { if (!value.isNullOrEmpty()) body[key] = value }
        put("current_item_id", c.itemId)
        put("category_id", c.categoryId)
        put("collection_id", c.collectionId)
        put("user_id", c.userId)
        put("anonymous_id", c.anonymousId)
        put("session_id", c.sessionId)
        put("locale", c.locale)
        if (c.cartItemIds != null) body["cart_item_ids"] = c.cartItemIds
        if (c.customContext != null) body["custom_context"] = c.customContext
        return body
    }

    /** Same placement, limit and context = same answer; who the anonymous visitor is does not count. */
    private fun cacheKey(placement: String, request: PlacementRequest): String {
        val ctx = request.context?.let { contextBody(it.copy(sessionId = null, anonymousId = null)) } ?: emptyMap()
        return "$placement|${request.limit ?: ""}|${Json.encode(ctx.toSortedMap())}"
    }

    /** LIKYLY down or overloaded, as opposed to a mistake in the call (4xx). */
    private fun isOutage(e: LikylyException): Boolean =
        e is NetworkException || e is TimeoutException || e is RateLimitException || (e.httpStatus ?: 0) >= 500
}
