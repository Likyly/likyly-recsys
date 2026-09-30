package com.likyly.model;

/**
 * One failing entry of a batch call.
 *
 * @param index position of the failing entry in the request (0-based)
 * @param id its item id / user id, when it had one
 * @param message 
 */
public record BatchError(
    int index,
    String id,
    String message) {
}
