package com.likyly.model;

import java.util.List;

/**
 * Send whatever you know: LIKYLY chooses the best strategy. Every field is optional.
 *
 * @param userId identified user
 * @param sessionId anonymous visitor
 * @param itemId the item being looked at (e.g. the product page)
 * @param viewedItemIds recently viewed items, oldest first
 * @param placement free-form label of where the recommendations will be shown
 * @param limit how many items to return (1-100, default 10)
 * @param debug diagnostic detail in explanations - secret key only
 */
public record RecommendationRequest(
    String userId,
    String sessionId,
    String itemId,
    List<String> viewedItemIds,
    String placement,
    Integer limit,
    Boolean debug) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private String userId;
        private String sessionId;
        private String itemId;
        private List<String> viewedItemIds;
        private String placement;
        private Integer limit;
        private Boolean debug;

        public Builder userId(String value) {
            this.userId = value;
            return this;
        }

        public Builder sessionId(String value) {
            this.sessionId = value;
            return this;
        }

        public Builder itemId(String value) {
            this.itemId = value;
            return this;
        }

        public Builder viewedItemIds(List<String> value) {
            this.viewedItemIds = value;
            return this;
        }

        public Builder placement(String value) {
            this.placement = value;
            return this;
        }

        public Builder limit(Integer value) {
            this.limit = value;
            return this;
        }

        public Builder debug(Boolean value) {
            this.debug = value;
            return this;
        }

        public RecommendationRequest build() {
            return new RecommendationRequest(userId, sessionId, itemId, viewedItemIds, placement, limit, debug);
        }
    }
}
