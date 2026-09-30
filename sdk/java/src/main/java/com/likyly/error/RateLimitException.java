package com.likyly.error;

/** 429 - see retryAfter(). */
public class RateLimitException extends ApiException {
    private static final long serialVersionUID = 1L;

    public RateLimitException(String message) {
        super(message);
    }

    public RateLimitException(String message, Integer statusCode, String requestId, Double retryAfter, Object body, Throwable cause) {
        super(message, statusCode, requestId, retryAfter, body, cause);
    }
}
