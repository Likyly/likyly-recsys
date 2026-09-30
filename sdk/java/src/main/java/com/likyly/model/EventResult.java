package com.likyly.model;

/**
 * Result of one tracked event.
 *
 * @param message 
 * @param eventId null unless you sent one
 * @param duplicate true if this eventId was already recorded - nothing was written
 */
public record EventResult(
    String message,
    String eventId,
    boolean duplicate) {
}
