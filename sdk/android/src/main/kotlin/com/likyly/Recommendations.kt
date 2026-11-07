package com.likyly

/**
 * `likyly.recommendations`. Use [get]: send what you know and LIKYLY picks the best strategy. The other
 * methods are the Advanced Recommendations - one strategy at a time. Prefer `likyly.placements` in an app.
 */
public class Recommendations internal constructor(private val core: Core) {

    /**
     * Recommendations for a user, an anonymous session, an item being viewed, viewed items - or nothing at all
     * (then you get what is popular). `strategy` in the response says what was used.
     */
    public suspend fun get(request: RecommendationRequest = RecommendationRequest()): RecommendationResponse {
        val body = LinkedHashMap<String, Any?>()
        request.userId?.takeIf { it.isNotEmpty() }?.let { body["user_id"] = requireId(it, "userId") }
        request.sessionId?.takeIf { it.isNotEmpty() }?.let { body["session_id"] = requireId(it, "sessionId") }
        request.itemId?.takeIf { it.isNotEmpty() }?.let { body["item_id"] = requireId(it, "itemId") }
        request.viewedItemIds?.let { ids ->
            ids.forEach { requireId(it, "viewedItemIds[]") }
            body["viewed_item_ids"] = ids
        }
        request.placement?.takeIf { it.isNotEmpty() }?.let { body["placement"] = it }
        request.limit?.let {
            if (it <= 0) throw ValidationException("limit must be a positive integer")
            body["count"] = it
        }
        if (request.debug) body["debug"] = true
        // A read: repeating it only mints another recommendation id, so it is retried.
        return send(Call("POST", "/getRec", body = body, idempotent = true))
    }

    // ---- Advanced Recommendations: ids travel in the URL path here, so they cannot contain "/". ----

    /** The most popular items - the fallback for a visitor with no history at all. */
    public suspend fun popular(limit: Int? = null, placement: String? = null, sessionId: String? = null): RecommendationResponse =
        advanced("/getRec/popular/${limitOf(limit)}", placement, sessionId)

    /** Items similar to one item (content similarity). No user needed. */
    public suspend fun similar(itemId: String, limit: Int? = null, placement: String? = null, sessionId: String? = null): RecommendationResponse {
        val item = requirePathSafeId(itemId, "itemId")
        return advanced("/getRec/content/${encodeSegment(item)}/${limitOf(limit)}", placement, sessionId)
    }

    /** What users with similar histories liked. Needs a trained model. */
    public suspend fun collaborative(userId: String, limit: Int? = null, placement: String? = null, sessionId: String? = null): RecommendationResponse {
        val user = requirePathSafeId(userId, "userId")
        return advanced("/getRec/collaborative/${encodeSegment(user)}/${limitOf(limit)}", placement, sessionId)
    }

    /** Similar items, personalized for a user. [alpha] (0-1) weighs the collaborative signal against content similarity. */
    public suspend fun hybrid(
        userId: String,
        itemId: String,
        limit: Int? = null,
        alpha: Double? = null,
        placement: String? = null,
        sessionId: String? = null,
    ): RecommendationResponse {
        val user = requirePathSafeId(userId, "userId")
        val item = requirePathSafeId(itemId, "itemId")
        val extra = if (alpha != null) listOf("alpha" to formatNumber(alpha)) else emptyList()
        return advanced("/getRec/hybrid/${encodeSegment(user)}/${encodeSegment(item)}/${limitOf(limit)}", placement, sessionId, extra)
    }

    /**
     * Recency-weighted recommendations from what was viewed: pass [viewedItemIds] (oldest first) OR [userId]
     * (LIKYLY's own history of that user's views) - exactly one.
     */
    public suspend fun session(
        viewedItemIds: List<String>? = null,
        userId: String? = null,
        limit: Int? = null,
        placement: String? = null,
        sessionId: String? = null,
    ): RecommendationResponse {
        val hasList = viewedItemIds != null
        val hasUser = !userId.isNullOrEmpty()
        if (hasList == hasUser) throw ValidationException("session needs either viewedItemIds or userId (exactly one)")
        val lim = limitOf(limit)
        if (hasUser) {
            val user = requirePathSafeId(userId, "userId")
            return advanced("/getRec/sessionForUser/${encodeSegment(user)}/$lim", placement, sessionId)
        }
        val ids = viewedItemIds!!
        if (ids.isEmpty()) throw ValidationException("viewedItemIds must contain at least one item id")
        for (id in ids) {
            requireId(id, "viewedItemIds[]")
            if (id.contains(',')) {
                throw ValidationException("an item id containing \",\" cannot be sent in this endpoint's comma-separated list - use recommendations.get")
            }
        }
        return advanced("/getRec/session", placement, sessionId, listOf("viewed_item_ids" to ids.joinToString(","), "count" to lim))
    }

    private suspend fun advanced(
        path: String,
        placement: String?,
        sessionId: String?,
        extra: List<Pair<String, String>> = emptyList(),
    ): RecommendationResponse {
        // response_format=object: always the same {recommendation_id, strategy, items} envelope.
        val query = ArrayList<Pair<String, String>>()
        query += "response_format" to "object"
        if (!placement.isNullOrEmpty()) query += "placement" to placement
        if (!sessionId.isNullOrEmpty()) query += "session_id" to sessionId
        query += extra
        return send(Call("GET", path, query = query, idempotent = true))
    }

    private suspend fun send(call: Call): RecommendationResponse {
        val w = core.request(call)
        return RecommendationResponse(
            recommendationId = w.string("recommendation_id") ?: "",
            strategy = w.string("strategy") ?: "",
            placement = w.string("placement"),
            items = recommendedItems(w.list("items")),
        )
    }

    private fun limitOf(limit: Int?): String {
        if (limit != null && limit <= 0) throw ValidationException("limit must be a positive integer")
        return (limit ?: DEFAULT_LIMIT).toString()
    }

    private fun formatNumber(d: Double): String = if (d == Math.rint(d)) d.toLong().toString() else d.toString()

    private companion object {
        const val DEFAULT_LIMIT = 10
    }
}
