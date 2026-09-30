package com.likyly.model;

/**
 * Result of {@code events().trackMany}.
 *
 * @param received 
 * @param accepted 
 * @param duplicates events skipped because their eventId was already recorded
 */
public record EventBatchResult(
    int received,
    int accepted,
    int duplicates) {
}
