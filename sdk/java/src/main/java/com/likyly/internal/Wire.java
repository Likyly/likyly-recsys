package com.likyly.internal;

import com.fasterxml.jackson.databind.JsonNode;
import com.likyly.model.BatchError;
import com.likyly.model.BatchResult;
import com.likyly.model.EventBatchResult;
import com.likyly.model.EventResult;
import com.likyly.model.Explanation;
import com.likyly.model.Item;
import com.likyly.model.RecommendationResponse;
import com.likyly.model.RecommendedItem;
import com.likyly.model.SimilarUser;
import com.likyly.model.User;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** JSON (snake_case) responses to the public types. {@code properties} objects are kept exactly as the API returned them. */
public final class Wire {
    private Wire() {
    }

    private static String str(JsonNode n, String key) {
        JsonNode v = n.get(key);
        return v == null || v.isNull() ? null : v.asText();
    }

    private static Double dbl(JsonNode n, String key) {
        JsonNode v = n.get(key);
        return v == null || v.isNull() ? null : v.asDouble();
    }

    private static Integer integer(JsonNode n, String key) {
        JsonNode v = n.get(key);
        return v == null || v.isNull() ? null : v.asInt();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> props(JsonNode n) {
        JsonNode v = n.get("properties");
        if (v == null || v.isNull()) {
            return new LinkedHashMap<>();
        }
        return HttpCore.mapper().convertValue(v, LinkedHashMap.class);
    }

    private static List<String> strings(JsonNode n, String key) {
        JsonNode v = n.get(key);
        if (v == null || v.isNull()) {
            return null;
        }
        List<String> out = new ArrayList<>();
        v.forEach(e -> out.add(e.asText()));
        return out;
    }

    public static Item item(JsonNode n) {
        return new Item(str(n, "item_id"), str(n, "title"), str(n, "description"), props(n));
    }

    public static User user(JsonNode n) {
        return new User(str(n, "user_id"), props(n));
    }

    public static BatchResult batch(JsonNode n) {
        List<BatchError> errors = new ArrayList<>();
        JsonNode list = n.get("errors");
        if (list != null) {
            list.forEach(e -> errors.add(new BatchError(e.get("index").asInt(), str(e, "id"), str(e, "message"))));
        }
        return new BatchResult(n.get("received").asInt(), n.get("succeeded").asInt(), n.get("failed").asInt(), errors);
    }

    public static EventResult eventResult(JsonNode n) {
        JsonNode dup = n.get("duplicate");
        return new EventResult(str(n, "message"), str(n, "event_id"), dup != null && dup.asBoolean());
    }

    public static EventBatchResult eventBatch(JsonNode n) {
        return new EventBatchResult(n.get("received").asInt(), n.get("accepted").asInt(), n.get("duplicates").asInt());
    }

    private static Explanation explanation(JsonNode n) {
        List<SimilarUser> similar = null;
        JsonNode su = n.get("similar_users");
        if (su != null && !su.isNull()) {
            similar = new ArrayList<>();
            for (JsonNode u : su) {
                List<String> shared = strings(u, "shared_item_ids");
                similar.add(new SimilarUser(str(u, "user_id"), shared == null ? List.of() : shared));
            }
        }
        return new Explanation(str(n, "reason"), dbl(n, "content_similarity"), dbl(n, "semantic_similarity"), dbl(n, "popularity_score"),
            integer(n, "interaction_count"), str(n, "interaction_label"), dbl(n, "collaborative_score"), strings(n, "source_item_ids"), similar);
    }

    public static RecommendationResponse recommendation(JsonNode n) {
        List<RecommendedItem> items = new ArrayList<>();
        for (JsonNode i : n.get("items")) {
            JsonNode ex = i.get("explanation");
            items.add(new RecommendedItem(str(i, "item_id"), dbl(i, "score"), str(i, "title"), str(i, "description"), props(i),
                ex == null || ex.isNull() ? null : explanation(ex)));
        }
        return new RecommendationResponse(str(n, "recommendation_id"), str(n, "strategy"), str(n, "placement"), items);
    }
}
