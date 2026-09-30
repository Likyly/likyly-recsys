package com.likyly.model;

import java.util.List;

/**
 * What recommendations().get() returns.
 *
 * @param recommendationId send it back on the impression / click / add_to_cart / purchase events for these items
 * @param strategy what LIKYLY used: hybrid, content, collaborative, session or popular
 * @param placement echo of the request
 * @param items 
 */
public record RecommendationResponse(
    String recommendationId,
    String strategy,
    String placement,
    List<RecommendedItem> items) {
}
