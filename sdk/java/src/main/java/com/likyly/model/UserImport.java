package com.likyly.model;

import java.util.Map;

/**
 * One entry of {@code users().importUsers}.
 *
 * @param userId your own id
 * @param properties free-form
 */
public record UserImport(
    String userId,
    Map<String, Object> properties) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private String userId;
        private Map<String, Object> properties;

        public Builder userId(String value) {
            this.userId = value;
            return this;
        }

        public Builder properties(Map<String, Object> value) {
            this.properties = value;
            return this;
        }

        public UserImport build() {
            return new UserImport(userId, properties);
        }
    }
}
