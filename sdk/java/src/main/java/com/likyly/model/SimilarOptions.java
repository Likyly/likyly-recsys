package com.likyly.model;

/**
 * Options of {@code recommendations().similar}.
 *
 * @param itemId the item to find neighbours of
 * @param limit how many items (default 10)
 * @param placement free-form label
 * @param sessionId attach the recommendation to an anonymous session, for attribution
 */
public record SimilarOptions(
    String itemId,
    Integer limit,
    String placement,
    String sessionId) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private String itemId;
        private Integer limit;
        private String placement;
        private String sessionId;

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

        public SimilarOptions build() {
            return new SimilarOptions(itemId, limit, placement, sessionId);
        }
    }
}
