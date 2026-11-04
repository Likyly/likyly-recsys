package com.likyly.model;

/**
 * Options of {@code events().identify}: the user who just logged in or signed up, and the session they browsed under anonymously.
 *
 * @param userId required
 * @param sessionId required
 */
public record IdentifyInput(
    String userId,
    String sessionId) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private String userId;
        private String sessionId;

        public Builder userId(String value) {
            this.userId = value;
            return this;
        }

        public Builder sessionId(String value) {
            this.sessionId = value;
            return this;
        }

        public IdentifyInput build() {
            return new IdentifyInput(userId, sessionId);
        }
    }
}
