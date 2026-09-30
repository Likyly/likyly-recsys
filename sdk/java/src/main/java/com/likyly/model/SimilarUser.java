package com.likyly.model;

import java.util.List;

/**
 * Another user with a similar history (debug + secret key only).
 *
 * @param userId 
 * @param sharedItemIds 
 */
public record SimilarUser(
    String userId,
    List<String> sharedItemIds) {
}
