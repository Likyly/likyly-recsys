package com.likyly.resources;

import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.likyly.error.ValidationException;
import com.likyly.internal.HttpCore;
import com.likyly.internal.Support;
import com.likyly.internal.Wire;
import com.likyly.model.EventBatchResult;
import com.likyly.model.EventInput;
import com.likyly.model.EventResult;
import com.likyly.model.TypedEventInput;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * {@code likyly.events()} - what your visitors do. Works with the <b>public</b> key from a browser-facing
 * service, or the secret key. {@link #track} is the one mechanism; {@link #view}, {@link #click}, ... are
 * shortcuts that call it with the matching event type. Event types are open strings ({@code favorite},
 * {@code share}, ...): the six helpers are conveniences, not a closed list.
 *
 * <p>A failed call is only retried automatically when it carries an {@code eventId} (then a replay is
 * harmless); without one, an ambiguous failure is thrown rather than risking a duplicate.
 */
public final class EventsResource {
    private static final Pattern TYPE = Pattern.compile("^[A-Za-z0-9_-]{1,64}$");
    private final HttpCore http;

    public EventsResource(HttpCore http) {
        this.http = http;
    }

    /** Records one event of any type. Needs {@code itemId} and a {@code userId} and/or a {@code sessionId}. */
    public EventResult track(String type, EventInput event) {
        ObjectNode body = wire(event.itemId(), event.userId(), event.sessionId(), event.recommendationId(), event.placement(),
            event.quantity(), event.occurredAt(), event.properties(), event.eventId());
        String path = "/events/" + HttpCore.encode(type(type));
        return Wire.eventResult(http.request("POST", path, Map.of(), body, event.eventId() != null).data());
    }

    /** Up to 1000 events in one call, each with its own {@code type}. All-or-nothing validation. */
    public EventBatchResult trackMany(List<TypedEventInput> events) {
        if (events == null || events.isEmpty()) {
            throw new ValidationException("events must be a non-empty list");
        }
        ArrayNode array = HttpCore.mapper().createArrayNode();
        boolean allIdempotent = true;
        for (TypedEventInput e : events) {
            ObjectNode entry = Support.object();
            entry.put("event_type", type(e.type()));
            entry.setAll(wire(e.itemId(), e.userId(), e.sessionId(), e.recommendationId(), e.placement(), e.quantity(), e.occurredAt(), e.properties(), e.eventId()));
            array.add(entry);
            allIdempotent &= e.eventId() != null;
        }
        ObjectNode body = Support.object();
        body.set("events", array);
        return Wire.eventBatch(http.request("POST", "/events/batch", Map.of(), body, allIdempotent).data());
    }

    /** The item was shown to the visitor (send the {@code recommendationId} it came with). */
    public EventResult impression(EventInput event) {
        return track("impression", event);
    }

    /** The visitor looked at the item (a product page, an article). */
    public EventResult view(EventInput event) {
        return track("view", event);
    }

    /** The visitor clicked the item. */
    public EventResult click(EventInput event) {
        return track("click", event);
    }

    public EventResult addToCart(EventInput event) {
        return track("add_to_cart", event);
    }

    public EventResult removeFromCart(EventInput event) {
        return track("remove_from_cart", event);
    }

    /** Set {@code eventId} (e.g. {@code purchase_<orderId>_<itemId>}) so a retry can never count the purchase twice. */
    public EventResult purchase(EventInput event) {
        return track("purchase", event);
    }

    private static String type(String type) {
        if (type == null || !TYPE.matcher(type).matches()) {
            throw new ValidationException("event type must be 1-64 characters of letters, digits, \"_\" or \"-\" (e.g. \"view\", \"add_to_cart\", \"favorite\")");
        }
        return type;
    }

    private static ObjectNode wire(String itemId, String userId, String sessionId, String recommendationId, String placement,
                                   Integer quantity, String occurredAt, Map<String, Object> properties, String eventId) {
        Support.requireId(itemId, "itemId");
        if (userId == null && sessionId == null) {
            throw new ValidationException("an event needs a userId or a sessionId (or both)");
        }
        if (userId != null) {
            Support.requireId(userId, "userId");
        }
        if (sessionId != null) {
            Support.requireId(sessionId, "sessionId");
        }
        ObjectNode node = Support.object();
        Support.put(node, "event_id", eventId);
        Support.put(node, "user_id", userId);
        Support.put(node, "session_id", sessionId);
        node.put("item_id", itemId);
        Support.put(node, "recommendation_id", recommendationId);
        Support.put(node, "placement", placement);
        Support.put(node, "quantity", quantity);
        Support.put(node, "occurred_at", occurredAt);
        Support.putProperties(node, properties);
        return node;
    }
}
