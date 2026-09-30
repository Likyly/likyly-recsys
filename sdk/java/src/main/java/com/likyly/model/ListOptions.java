package com.likyly.model;

/**
 * Pagination: {@code limit} + {@code offset}.
 *
 * @param limit page size (1-1000); null = the API's default
 * @param offset rows to skip
 */
public record ListOptions(
    Integer limit,
    Integer offset) {

    public static Builder builder() {
        return new Builder();
    }

    public static ListOptions limit(int limit) {
        return new ListOptions(limit, null);
    }

    /** Fluent builder. */
    public static final class Builder {
        private Integer limit;
        private Integer offset;

        public Builder limit(Integer value) {
            this.limit = value;
            return this;
        }

        public Builder offset(Integer value) {
            this.offset = value;
            return this;
        }

        public ListOptions build() {
            return new ListOptions(limit, offset);
        }
    }
}
