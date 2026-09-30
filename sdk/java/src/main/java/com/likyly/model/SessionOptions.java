package com.likyly.model;

import java.util.List;

/**
 * Options of {@code recommendations().session}.
 *
 * @param viewedItemIds an explicit list of viewed items, oldest first
 * @param userId OR: LIKYLY's own history of this user's views - exactly one of the two
 * @param limit how many items (default 10)
 * @param placement free-form label
 * @param sessionId attach the recommendation to an anonymous session, for attribution
 */
public record SessionOptions(
    List<String> viewedItemIds,
    String userId,
    Integer limit,
    String placement,
    String sessionId) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private List<String> viewedItemIds;
        private String userId;
        private Integer limit;
        private String placement;
        private String sessionId;

        public Builder viewedItemIds(List<String> value) {
            this.viewedItemIds = value;
            return this;
        }

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

        public SessionOptions build() {
            return new SessionOptions(viewedItemIds, userId, limit, placement, sessionId);
        }
    }
}
