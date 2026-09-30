package com.likyly.model;

import java.util.Map;

/**
 * One entry of a batch upsert ({@code items().upsertMany}).
 *
 * @param itemId your own id
 * @param title required
 * @param description optional
 * @param properties free-form
 */
public record ItemImport(
    String itemId,
    String title,
    String description,
    Map<String, Object> properties) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private String itemId;
        private String title;
        private String description;
        private Map<String, Object> properties;

        public Builder itemId(String value) {
            this.itemId = value;
            return this;
        }

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

        public ItemImport build() {
            return new ItemImport(itemId, title, description, properties);
        }
    }
}
