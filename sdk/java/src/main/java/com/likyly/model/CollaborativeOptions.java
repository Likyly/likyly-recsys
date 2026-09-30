package com.likyly.model;

/**
 * Options of {@code recommendations().collaborative}.
 *
 * @param userId the user
 * @param limit how many items (default 10)
 * @param placement free-form label
 * @param sessionId attach the recommendation to an anonymous session, for attribution
 */
public record CollaborativeOptions(
    String userId,
    Integer limit,
    String placement,
    String sessionId) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private String userId;
        private Integer limit;
        private String placement;
        private String sessionId;

        public Builder userId(String value) {
            this.userId = value;
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

        public CollaborativeOptions build() {
            return new CollaborativeOptions(userId, limit, placement, sessionId);
        }
    }
}
