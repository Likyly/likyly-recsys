package com.likyly.model;

import java.util.Map;

/**
 * What you send to create or replace an item. Only {@code title} is required.
 *
 * @param title required
 * @param description optional; feeds content similarity
 * @param properties category, brand, price, ... - keys are yours
 */
public record ItemInput(
    String title,
    String description,
    Map<String, Object> properties) {

    public static Builder builder() {
        return new Builder();
    }

    public static ItemInput of(String title) {
        return new ItemInput(title, null, null);
    }

    /** Fluent builder. */
    public static final class Builder {
        private String title;
        private String description;
        private Map<String, Object> properties;

        public Builder title(String value) {
            this.title = value;
            return this;
        }

        public Builder description(String value) {
            this.description = value;
            return this;
        }

        public Builder properties(Map<String, Object> value) {
            this.properties = value;
            return this;
        }

        public ItemInput build() {
            return new ItemInput(title, description, properties);
        }
    }
}
