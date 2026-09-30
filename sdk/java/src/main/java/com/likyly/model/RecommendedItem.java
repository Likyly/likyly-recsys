package com.likyly.model;

import java.util.Map;

/**
 * One recommended item.
 *
 * @param itemId 
 * @param score higher is better; comparable within one response only
 * @param title 
 * @param description 
 * @param properties 
 * @param explanation may be null
 */
public record RecommendedItem(
    String itemId,
    Double score,
    String title,
    String description,
    Map<String, Object> properties,
    Explanation explanation) {
}
