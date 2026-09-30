package com.likyly.model;

import java.util.Map;

/**
 * What you send to create or replace a user profile.
 *
 * @param properties country, segment, language, ... - whatever describes your users
 */
public record UserInput(
    Map<String, Object> properties) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private Map<String, Object> properties;

        public Builder properties(Map<String, Object> value) {
            this.properties = value;
            return this;
        }

        public UserInput build() {
            return new UserInput(properties);
        }
    }
}
