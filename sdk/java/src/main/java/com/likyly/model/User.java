package com.likyly.model;

import java.util.Map;

/**
 * A user profile.
 *
 * @param userId 
 * @param properties free-form: country, segment, ...
 */
public record User(
    String userId,
    Map<String, Object> properties) {
}
