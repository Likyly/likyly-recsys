package com.likyly

import java.util.Date

/** Your own free-form fields. Keys are yours and are never renamed; values are JSON values (String, Number, Boolean, null, List, Map). */
public typealias Properties = Map<String, Any?>

/** What a visitor did with an item. Needs [itemId] and a [userId] or a [sessionId] (or both). */
public data class EventInput(
    val itemId: String,
    val userId: String? = null,
    val sessionId: String? = null,
    /** Your idempotency key (e.g. "purchase_<orderId>_<itemId>"): a replay is ignored, and the call is retried safely. */
    val eventId: String? = null,
    /** The recommendationId the item came with, to attribute the event to that recommendation. */
    val recommendationId: String? = null,
    val placement: String? = null,
    val quantity: Int? = null,
    /** When it happened, if not now. */
    val occurredAt: Date? = null,
    val properties: Properties? = null,
)

/** One event of a [Events.trackMany] batch, with its own type. */
public data class TypedEvent(val type: String, val event: EventInput)

public data class EventResult(val message: String, val eventId: String?, val duplicate: Boolean)

public data class EventBatchResult(val received: Int, val accepted: Int, val duplicates: Int)

public data class IdentifyResult(val linkedInteractions: Int)

/** What you know about where the placement is shown. Send whatever you have; nulls are left out. */
public data class PlacementContext(
    val itemId: String? = null,
    val categoryId: String? = null,
    val collectionId: String? = null,
    val userId: String? = null,
    val anonymousId: String? = null,
    val sessionId: String? = null,
    val cartItemIds: List<String>? = null,
    val locale: String? = null,
    /** Passed through untouched. */
    val customContext: Map<String, Any?>? = null,
)

public data class PlacementRequest(
    /** The placement's slug, configured once in LIKYLY (dashboard or MCP admin tools). */
    val placement: String,
    val context: PlacementContext? = null,
    val limit: Int? = null,
)

public data class PlacementResponse(
    /** Send it back on the impression/click/... events that follow. */
    val recommendationId: String,
    val placement: String,
    val strategyUsed: String,
    val items: List<RecommendedItem>,
)

public data class RecommendationResponse(
    val recommendationId: String,
    val strategy: String,
    val placement: String?,
    val items: List<RecommendedItem>,
)

public data class RecommendedItem(
    val itemId: String,
    val score: Double?,
    val title: String?,
    val description: String?,
    val properties: Properties,
    val explanation: Explanation?,
)

public data class Explanation(
    val reason: String,
    val contentSimilarity: Double?,
    val semanticSimilarity: Double?,
    val popularityScore: Double?,
    val interactionCount: Int?,
    val interactionLabel: String?,
    val collaborativeScore: Double?,
    val sourceItemIds: List<String>?,
    val similarUsers: List<SimilarUser>?,
)

public data class SimilarUser(val userId: String, val sharedItemIds: List<String>)

/** Input of [Recommendations.get]: send what you know - LIKYLY picks the strategy. */
public data class RecommendationRequest(
    val userId: String? = null,
    val sessionId: String? = null,
    val itemId: String? = null,
    val viewedItemIds: List<String>? = null,
    val placement: String? = null,
    val limit: Int? = null,
    val debug: Boolean = false,
)

internal fun recommendedItems(raw: List<Any?>?): List<RecommendedItem> = (raw ?: emptyList()).mapNotNull { entry ->
    @Suppress("UNCHECKED_CAST")
    val it = entry as? Map<String, Any?> ?: return@mapNotNull null
    RecommendedItem(
        itemId = it.string("item_id") ?: "",
        score = it.double("score"),
        title = it.string("title"),
        description = it.string("description"),
        properties = it.obj("properties") ?: emptyMap(),
        explanation = it.obj("explanation")?.let { e ->
            Explanation(
                reason = e.string("reason") ?: "",
                contentSimilarity = e.double("content_similarity"),
                semanticSimilarity = e.double("semantic_similarity"),
                popularityScore = e.double("popularity_score"),
                interactionCount = e.int("interaction_count"),
                interactionLabel = e.string("interaction_label"),
                collaborativeScore = e.double("collaborative_score"),
                sourceItemIds = e.stringList("source_item_ids"),
                similarUsers = e.list("similar_users")?.mapNotNull { u ->
                    @Suppress("UNCHECKED_CAST")
                    val m = u as? Map<String, Any?> ?: return@mapNotNull null
                    SimilarUser(m.string("user_id") ?: "", m.stringList("shared_item_ids") ?: emptyList())
                },
            )
        },
    )
}
