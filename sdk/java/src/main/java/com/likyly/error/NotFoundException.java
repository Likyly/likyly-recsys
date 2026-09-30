package com.likyly.error;

/** 404. */
public class NotFoundException extends ApiException {
    private static final long serialVersionUID = 1L;

    public NotFoundException(String message) {
        super(message);
    }

    public NotFoundException(String message, Integer statusCode, String requestId, Double retryAfter, Object body, Throwable cause) {
        super(message, statusCode, requestId, retryAfter, body, cause);
    }
}
