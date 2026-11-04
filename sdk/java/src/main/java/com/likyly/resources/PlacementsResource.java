package com.likyly.resources;

import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.likyly.internal.HttpCore;
import com.likyly.internal.Support;
import com.likyly.internal.Wire;
import com.likyly.model.PlacementContext;
import com.likyly.model.PlacementRecommendOptions;
import com.likyly.model.PlacementRecommendResponse;
import java.util.Map;

/**
 * {@code likyly.placements()} - the recommended way to ask for recommendations. A placement ("what to recommend,
 * where, with what fallback and filters") is configured once - with the LIKYLY MCP admin tools or the dashboard,
 * never from this SDK - and called by its slug from then on. {@link RecommendationsResource} remains available for
 * per-call use with no stored configuration.
 */
public final class PlacementsResource {
    private final HttpCore http;

    public PlacementsResource(HttpCore http) {
        this.http = http;
    }

    /**
     * Recommendations for one placement: send whatever context you have ({@code itemId}, {@code userId},
     * {@code sessionId}, ...) - the placement's own configuration decides the strategy, fallback and filters.
     * {@code strategyUsed} in the response says what it used; send {@code recommendationId} back on the events that follow.
     */
    public PlacementRecommendResponse recommend(PlacementRecommendOptions options) {
        String placement = Support.requireId(options.placement(), "placement");
        ObjectNode body = Support.object();
        if (options.context() != null) {
            body.set("context", context(options.context()));
        }
        Support.put(body, "limit", options.limit());
        // a read in effect: each call mints a fresh recommendationId, so a retry is harmless
        String path = "/placements/" + HttpCore.encode(placement) + "/recommend";
        return Wire.placementRecommendation(http.request("POST", path, Map.of(), body, true).data());
    }

    private static ObjectNode context(PlacementContext c) {
        ObjectNode node = Support.object();
        Support.put(node, "current_item_id", c.itemId());
        Support.put(node, "category_id", c.categoryId());
        Support.put(node, "collection_id", c.collectionId());
        Support.put(node, "user_id", c.userId());
        Support.put(node, "anonymous_id", c.anonymousId());
        Support.put(node, "session_id", c.sessionId());
        if (c.cartItemIds() != null) {
            ArrayNode ids = HttpCore.mapper().createArrayNode();
            c.cartItemIds().forEach(id -> ids.add(Support.requireId(id, "cartItemIds[]")));
            node.set("cart_item_ids", ids);
        }
        Support.put(node, "locale", c.locale());
        if (c.customContext() != null) {
            node.set("custom_context", HttpCore.mapper().valueToTree(c.customContext()));
        }
        return node;
    }
}
