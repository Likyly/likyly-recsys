package com.likyly.error;

/** 403 - e.g. a public key used for a secret-key operation, or a plan limit. */
public class PermissionDeniedException extends ApiException {
    private static final long serialVersionUID = 1L;

    public PermissionDeniedException(String message) {
        super(message);
    }

    public PermissionDeniedException(String message, Integer statusCode, String requestId, Double retryAfter, Object body, Throwable cause) {
        super(message, statusCode, requestId, retryAfter, body, cause);
    }
}
