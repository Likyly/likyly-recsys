package com.likyly.resources;

import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.likyly.error.ValidationException;
import com.likyly.internal.HttpCore;
import com.likyly.internal.Support;
import com.likyly.internal.Wire;
import com.likyly.model.CollaborativeOptions;
import com.likyly.model.HybridOptions;
import com.likyly.model.PopularOptions;
import com.likyly.model.RecommendationRequest;
import com.likyly.model.RecommendationResponse;
import com.likyly.model.SessionOptions;
import com.likyly.model.SimilarOptions;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * {@code likyly.recommendations()}. Use {@link #get}: send what you know and LIKYLY picks the best strategy.
 * The other methods are the <i>Advanced Recommendations</i> - one strategy at a time.
 */
public final class RecommendationsResource {
    private final HttpCore http;

    public RecommendationsResource(HttpCore http) {
        this.http = http;
    }

    /** Nothing known about the visitor: you get what is popular. */
    public RecommendationResponse get() {
        return get(RecommendationRequest.builder().build());
    }

    /**
     * Recommendations for a user, an anonymous session, an item being viewed, viewed items - or nothing at all
     * (then you get what is popular). {@code strategy} in the response says what LIKYLY used; send
     * {@code recommendationId} back on the events that follow. {@code limit} is 1-100 (default 10).
     */
    public RecommendationResponse get(RecommendationRequest request) {
        ObjectNode body = Support.object();
        if (request.userId() != null) {
            body.put("user_id", Support.requireId(request.userId(), "userId"));
        }
        if (request.sessionId() != null) {
            body.put("session_id", Support.requireId(request.sessionId(), "sessionId"));
        }
        if (request.itemId() != null) {
            body.put("item_id", Support.requireId(request.itemId(), "itemId"));
        }
        if (request.viewedItemIds() != null) {
            ArrayNode ids = HttpCore.mapper().createArrayNode();
            request.viewedItemIds().forEach(id -> ids.add(Support.requireId(id, "viewedItemIds[]")));
            body.set("viewed_item_ids", ids);
        }
        Support.put(body, "placement", request.placement());
        Support.put(body, "count", request.limit());
        if (Boolean.TRUE.equals(request.debug())) {
            body.put("debug", true);
        }
        return Wire.recommendation(http.request("POST", "/getRec", Map.of(), body, true).data());
    }

    // ---- Advanced Recommendations: ids travel in the URL path here, so they cannot contain "/" ----

    /** The most popular items - the fallback for a visitor with no history at all. */
    public RecommendationResponse popular() {
        return popular(new PopularOptions(null, null, null));
    }

    public RecommendationResponse popular(PopularOptions o) {
        return advanced("/getRec/popular/" + Support.limit(o.limit()), o.placement(), o.sessionId(), Map.of());
    }

    /** Items similar to one item (content similarity). No user needed. */
    public RecommendationResponse similar(SimilarOptions o) {
        String path = "/getRec/content/" + HttpCore.encode(Support.requirePathSafeId(o.itemId(), "itemId")) + "/" + Support.limit(o.limit());
        return advanced(path, o.placement(), o.sessionId(), Map.of());
    }

    /** What users with similar histories liked. Needs a trained model. */
    public RecommendationResponse collaborative(CollaborativeOptions o) {
        String path = "/getRec/collaborative/" + HttpCore.encode(Support.requirePathSafeId(o.userId(), "userId")) + "/" + Support.limit(o.limit());
        return advanced(path, o.placement(), o.sessionId(), Map.of());
    }

    /** Similar items, personalized for a user. {@code alpha} (0-1) weighs the collaborative signal against content similarity. */
    public RecommendationResponse hybrid(HybridOptions o) {
        String path = "/getRec/hybrid/" + HttpCore.encode(Support.requirePathSafeId(o.userId(), "userId")) + "/"
            + HttpCore.encode(Support.requirePathSafeId(o.itemId(), "itemId")) + "/" + Support.limit(o.limit());
        Map<String, String> extra = new LinkedHashMap<>();
        if (o.alpha() != null) {
            extra.put("alpha", String.valueOf(o.alpha()));
        }
        return advanced(path, o.placement(), o.sessionId(), extra);
    }

    /**
     * Recency-weighted recommendations from what was viewed: pass {@code viewedItemIds} (an explicit list, oldest first)
     * <b>or</b> {@code userId} (LIKYLY's own history of that user's views) - exactly one.
     */
    public RecommendationResponse session(SessionOptions o) {
        boolean hasList = o.viewedItemIds() != null;
        boolean hasUser = o.userId() != null;
        if (hasList == hasUser) {
            throw new ValidationException("session() needs either viewedItemIds or userId (exactly one)");
        }
        if (hasUser) {
            String path = "/getRec/sessionForUser/" + HttpCore.encode(Support.requirePathSafeId(o.userId(), "userId")) + "/" + Support.limit(o.limit());
            return advanced(path, o.placement(), o.sessionId(), Map.of());
        }
        List<String> ids = o.viewedItemIds().stream().map(id -> Support.requireId(id, "viewedItemIds[]")).toList();
        if (ids.isEmpty()) {
            throw new ValidationException("viewedItemIds must contain at least one item id");
        }
        if (ids.stream().anyMatch(id -> id.contains(","))) {
            throw new ValidationException("an item id containing \",\" cannot be sent in this endpoint's comma-separated list - use recommendations().get()");
        }
        Map<String, String> extra = new LinkedHashMap<>();
        extra.put("viewed_item_ids", String.join(",", ids));
        extra.put("count", String.valueOf(Support.limit(o.limit())));
        return advanced("/getRec/session", o.placement(), o.sessionId(), extra);
    }

    private RecommendationResponse advanced(String path, String placement, String sessionId, Map<String, String> extra) {
        // response_format=object: always the same {recommendation_id, strategy, items} envelope
        Map<String, String> query = new LinkedHashMap<>();
        query.put("response_format", "object");
        if (placement != null) {
            query.put("placement", placement);
        }
        if (sessionId != null) {
            query.put("session_id", sessionId);
        }
        query.putAll(extra);
        return Wire.recommendation(http.request("GET", path, query, null, true).data());
    }
}
