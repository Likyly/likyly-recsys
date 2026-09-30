package com.likyly.model;

import java.util.List;

/**
 * Result of a batch call.
 *
 * @param received 
 * @param succeeded 
 * @param failed 
 * @param errors a failing entry is reported here, it does not throw
 */
public record BatchResult(
    int received,
    int succeeded,
    int failed,
    List<BatchError> errors) {
}
