package com.likyly.model;

import java.util.Map;

/**
 * An interaction. {@code itemId} and at least one of {@code userId} / {@code sessionId} are required; sending both ties an anonymous session to the user.
 *
 * @param itemId required
 * @param userId identified user
 * @param sessionId anonymous visitor / browsing session - no login needed
 * @param recommendationId the recommendationId of the recommendation that surfaced this item - enables attribution
 * @param placement free-form label of where this happened: homepage, product_page, cart, ...
 * @param quantity units, seconds watched, ...
 * @param occurredAt ISO 8601; defaults to the server's time
 * @param properties price, currency, orderId, ... - keys are yours, never renamed
 * @param eventId idempotency key, unique per account: a replay records nothing and returns duplicate=true; set it on purchases
 */
public record EventInput(
    String itemId,
    String userId,
    String sessionId,
    String recommendationId,
    String placement,
    Integer quantity,
    String occurredAt,
    Map<String, Object> properties,
    String eventId) {

    public static Builder builder() {
        return new Builder();
    }


    /** Fluent builder. */
    public static final class Builder {
        private String itemId;
        private String userId;
        private String sessionId;
        private String recommendationId;
        private String placement;
        private Integer quantity;
        private String occurredAt;
        private Map<String, Object> properties;
        private String eventId;

        public Builder itemId(String value) {
            this.itemId = value;
            return this;
        }

        public Builder userId(String value) {
            this.userId = value;
            return this;
        }

        public Builder sessionId(String value) {
            this.sessionId = value;
            return this;
        }

        public Builder recommendationId(String value) {
            this.recommendationId = value;
            return this;
        }

        public Builder placement(String value) {
            this.placement = value;
            return this;
        }

        public Builder quantity(Integer value) {
            this.quantity = value;
            return this;
        }

        /** The time as an {@link java.time.Instant} (sent as ISO 8601). */
        public Builder occurredAt(java.time.Instant value) {
            this.occurredAt = value == null ? null : value.toString();
            return this;
        }

        public Builder occurredAt(String value) {
            this.occurredAt = value;
            return this;
        }

        public Builder properties(Map<String, Object> value) {
            this.properties = value;
            return this;
        }

        public Builder eventId(String value) {
            this.eventId = value;
            return this;
        }

        public EventInput build() {
            return new EventInput(itemId, userId, sessionId, recommendationId, placement, quantity, occurredAt, properties, eventId);
        }
    }
}
