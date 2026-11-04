package com.likyly.model;

import java.util.List;
import java.util.Map;

/**
 * What you know about the moment. Every field is optional unless the placement's own configuration requires it.
 *
 * @param itemId the product / article / listing currently being viewed
 * @param categoryId the category being browsed
 * @param collectionId the collection being browsed
 * @param userId identified user
 * @param anonymousId stable anonymous visitor id - used like {@code sessionId} when {@code sessionId} is omitted
 * @param sessionId anonymous visitor / browsing session
 * @param cartItemIds items currently in the cart
 * @param locale e.g. {@code fr-FR}
 * @param customContext anything else the placement's filters use - keys are yours, never renamed
 */
public record PlacementContext(
    String itemId,
    String categoryId,
    String collectionId,
    String userId,
    String anonymousId,
    String sessionId,
    List<String> cartItemIds,
    String locale,
    Map<String, Object> customContext) {

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private String itemId;
        private String categoryId;
        private String collectionId;
        private String userId;
        private String anonymousId;
        private String sessionId;
        private List<String> cartItemIds;
        private String locale;
        private Map<String, Object> customContext;

        public Builder itemId(String value) {
            this.itemId = value;
            return this;
        }

        public Builder categoryId(String value) {
            this.categoryId = value;
            return this;
        }

        public Builder collectionId(String value) {
            this.collectionId = value;
            return this;
        }

        public Builder userId(String value) {
            this.userId = value;
            return this;
        }

        public Builder anonymousId(String value) {
            this.anonymousId = value;
            return this;
        }

        public Builder sessionId(String value) {
            this.sessionId = value;
            return this;
        }

        public Builder cartItemIds(List<String> value) {
            this.cartItemIds = value;
            return this;
        }

        public Builder locale(String value) {
            this.locale = value;
            return this;
        }

        public Builder customContext(Map<String, Object> value) {
            this.customContext = value;
            return this;
        }

        public PlacementContext build() {
            return new PlacementContext(itemId, categoryId, collectionId, userId, anonymousId, sessionId, cartItemIds, locale, customContext);
        }
    }
}
