package com.likyly.model;

/**
 * Result of {@code events().identify}.
 *
 * @param linkedInteractions how many of the session's past interactions now belong to the user
 */
public record IdentifyResult(
    int linkedInteractions) {
}
