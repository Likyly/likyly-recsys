package com.likyly

import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * What your visitors do: `likyly.events`. [track] is the one mechanism; [view], [click], ... are shortcuts
 * that call it with the matching event type.
 *
 * Retries: a failed call is only retried automatically when it carries an eventId (then a replay is harmless);
 * without one, an ambiguous failure is thrown rather than risking a duplicate.
 */
public class Events internal constructor(private val core: Core) {

    /** Records one event of any type: "view", "click", ... or your own ("favorite", "share", ...). */
    public suspend fun track(type: String, event: EventInput): EventResult {
        val t = requireEventType(type)
        val body = eventBody(event)
        val w = core.request(Call("POST", "/events/${encodeSegment(t)}", body = body, idempotent = !event.eventId.isNullOrEmpty()))
        return EventResult(w.string("message") ?: "", w.string("event_id"), w.bool("duplicate") ?: false)
    }

    /** Records up to 1000 events in one call, each with its own type. Validation is all-or-nothing. */
    public suspend fun trackMany(events: List<TypedEvent>): EventBatchResult {
        if (events.isEmpty()) throw ValidationException("events must be a non-empty list")
        val entries = events.map { e ->
            val t = requireEventType(e.type)
            LinkedHashMap<String, Any?>().apply {
                put("event_type", t)
                putAll(eventBody(e.event))
            }
        }
        val allKeyed = events.all { !it.event.eventId.isNullOrEmpty() }
        val w = core.request(Call("POST", "/events/batch", body = mapOf("events" to entries), idempotent = allKeyed))
        return EventBatchResult(w.int("received") ?: 0, w.int("accepted") ?: 0, w.int("duplicates") ?: 0)
    }

    /**
     * Ties an anonymous session to the user who just signed in (or signed up): the session's past interactions
     * now count for [userId]. Call it once at login; calling it again is harmless, so it is retried.
     */
    public suspend fun identify(userId: String, sessionId: String): IdentifyResult {
        val body = mapOf("user_id" to requireId(userId, "userId"), "session_id" to requireId(sessionId, "sessionId"))
        val w = core.request(Call("POST", "/events/identify", body = body, idempotent = true))
        return IdentifyResult(w.int("linked_interactions") ?: 0)
    }

    /** The item was shown to the visitor (send the recommendationId it came with). */
    public suspend fun impression(event: EventInput): EventResult = track("impression", event)

    /** The visitor looked at the item (a product screen, an article). */
    public suspend fun view(event: EventInput): EventResult = track("view", event)

    /** The visitor tapped the item. */
    public suspend fun click(event: EventInput): EventResult = track("click", event)

    /** The visitor added the item to their cart. */
    public suspend fun addToCart(event: EventInput): EventResult = track("add_to_cart", event)

    /** The visitor removed the item from their cart. */
    public suspend fun removeFromCart(event: EventInput): EventResult = track("remove_from_cart", event)

    /** The visitor bought the item. Set eventId (e.g. "purchase_<orderId>_<itemId>") so a retry can never count it twice. */
    public suspend fun purchase(event: EventInput): EventResult = track("purchase", event)

    private fun eventBody(e: EventInput): Map<String, Any?> {
        val itemId = requireId(e.itemId, "itemId")
        if (e.userId.isNullOrEmpty() && e.sessionId.isNullOrEmpty()) {
            throw ValidationException("an event needs a userId or a sessionId (or both)")
        }
        val body = LinkedHashMap<String, Any?>()
        if (!e.eventId.isNullOrEmpty()) body["event_id"] = e.eventId
        if (!e.userId.isNullOrEmpty()) body["user_id"] = requireId(e.userId, "userId")
        if (!e.sessionId.isNullOrEmpty()) body["session_id"] = requireId(e.sessionId, "sessionId")
        body["item_id"] = itemId
        if (!e.recommendationId.isNullOrEmpty()) body["recommendation_id"] = e.recommendationId
        if (!e.placement.isNullOrEmpty()) body["placement"] = e.placement
        if (e.quantity != null) body["quantity"] = e.quantity
        if (e.occurredAt != null) {
            // RFC 3339, milliseconds only when there are some (as the other SDKs send it).
            val pattern = if (e.occurredAt.time % 1000 == 0L) "yyyy-MM-dd'T'HH:mm:ss'Z'" else "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"
            val fmt = SimpleDateFormat(pattern, Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
            body["occurred_at"] = fmt.format(e.occurredAt)
        }
        if (e.properties != null) body["properties"] = e.properties
        return body
    }
}
