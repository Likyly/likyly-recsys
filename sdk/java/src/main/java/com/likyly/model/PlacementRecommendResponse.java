package com.likyly.model;

import java.util.List;

/**
 * What placements().recommend() returns.
 *
 * @param recommendationId send it back on the impression / click / add_to_cart / purchase events for these items
 * @param placement the placement's slug
 * @param strategyUsed what the placement actually used: hybrid, content, collaborative, session or popular
 * @param items 
 */
public record PlacementRecommendResponse(
    String recommendationId,
    String placement,
    String strategyUsed,
    List<RecommendedItem> items) {
}
