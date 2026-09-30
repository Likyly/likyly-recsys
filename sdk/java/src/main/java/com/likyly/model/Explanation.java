package com.likyly.model;

import java.util.List;

/**
 * Why an item was recommended.
 *
 * @param reason always present, human-readable
 * @param contentSimilarity 
 * @param semanticSimilarity 
 * @param popularityScore 
 * @param interactionCount 
 * @param interactionLabel 
 * @param collaborativeScore 
 * @param sourceItemIds 
 * @param similarUsers only with debug and a secret key
 */
public record Explanation(
    String reason,
    Double contentSimilarity,
    Double semanticSimilarity,
    Double popularityScore,
    Integer interactionCount,
    String interactionLabel,
    Double collaborativeScore,
    List<String> sourceItemIds,
    List<SimilarUser> similarUsers) {
}
