package com.likyly.model;

/**
 * Options of {@code placements().recommend}.
 *
 * @param placement the placement's slug, e.g. {@code pdp-related} - required
 * @param context what you know about the moment; may be null
 * @param limit overrides the placement's own configured limit for this call
 */
public record PlacementRecommendOptions(
    String placement,
    PlacementContext context,
    Integer limit) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private String placement;
        private PlacementContext context;
        private Integer limit;

        public Builder placement(String value) {
            this.placement = value;
            return this;
        }

        public Builder context(PlacementContext value) {
            this.context = value;
            return this;
        }

        public Builder limit(Integer value) {
            this.limit = value;
            return this;
        }

        public PlacementRecommendOptions build() {
            return new PlacementRecommendOptions(placement, context, limit);
        }
    }
}
