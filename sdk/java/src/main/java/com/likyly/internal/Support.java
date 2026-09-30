package com.likyly.internal;

import com.fasterxml.jackson.databind.node.ObjectNode;
import com.likyly.error.ValidationException;
import java.util.Map;

/** Validation and JSON helpers shared by the resources. */
public final class Support {
    private Support() {
    }

    /** Ids are opaque, non-empty strings - never coerced to numbers. */
    public static String requireId(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new ValidationException(name + " must be a non-empty string (your own identifier, e.g. \"SKU-123\")");
        }
        return value;
    }

    /** Advanced recommendation endpoints carry ids in the URL path, where '/' cannot be used. */
    public static String requirePathSafeId(String value, String name) {
        String id = requireId(value, name);
        if (id.contains("/")) {
            throw new ValidationException(name + " \"" + id + "\" contains \"/\", which this endpoint cannot carry in its URL path - "
                + "use recommendations().get(), which takes ids in the request body");
        }
        return id;
    }

    public static int limit(Integer limit) {
        int value = limit == null ? 10 : limit;
        if (value < 1) {
            throw new ValidationException("limit must be a positive integer");
        }
        return value;
    }

    public static ObjectNode object() {
        return HttpCore.mapper().createObjectNode();
    }

    /** Puts a value when it is not null; {@code properties} maps are copied verbatim (their keys are the developer's). */
    public static void put(ObjectNode node, String key, Object value) {
        if (value == null) {
            return;
        }
        if (value instanceof String s) {
            node.put(key, s);
        } else if (value instanceof Integer i) {
            node.put(key, i);
        } else if (value instanceof Long l) {
            node.put(key, l);
        } else if (value instanceof Double d) {
            node.put(key, d);
        } else if (value instanceof Boolean b) {
            node.put(key, b);
        } else {
            node.set(key, HttpCore.mapper().valueToTree(value));
        }
    }

    public static void putProperties(ObjectNode node, Map<String, Object> properties) {
        if (properties != null) {
            node.set("properties", HttpCore.mapper().valueToTree(properties));
        }
    }
}
