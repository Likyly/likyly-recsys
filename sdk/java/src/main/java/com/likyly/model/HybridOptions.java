package com.likyly.model;

/**
 * Options of {@code recommendations().hybrid}.
 *
 * @param userId the user
 * @param itemId the item
 * @param limit how many items (default 10)
 * @param placement free-form label
 * @param sessionId attach the recommendation to an anonymous session, for attribution
 * @param alpha weight of the collaborative signal against content similarity: 0 = pure content, 1 = pure collaborative (default 0.5)
 */
public record HybridOptions(
    String userId,
    String itemId,
    Integer limit,
    String placement,
    String sessionId,
    Double alpha) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private String userId;
        private String itemId;
        private Integer limit;
        private String placement;
        private String sessionId;
        private Double alpha;

        public Builder userId(String value) {
            this.userId = value;
            return this;
        }

        public Builder itemId(String value) {
            this.itemId = value;
            return this;
        }

        public Builder limit(Integer value) {
            this.limit = value;
            return this;
        }

        public Builder placement(String value) {
            this.placement = value;
            return this;
        }

        public Builder sessionId(String value) {
            this.sessionId = value;
            return this;
        }

        public Builder alpha(Double value) {
            this.alpha = value;
            return this;
        }

        public HybridOptions build() {
            return new HybridOptions(userId, itemId, limit, placement, sessionId, alpha);
        }
    }
}
