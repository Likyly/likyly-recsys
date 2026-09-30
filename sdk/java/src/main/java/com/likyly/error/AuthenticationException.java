package com.likyly.error;

/** 401 - missing, invalid or revoked API key. */
public class AuthenticationException extends ApiException {
    private static final long serialVersionUID = 1L;

    public AuthenticationException(String message) {
        super(message);
    }

    public AuthenticationException(String message, Integer statusCode, String requestId, Double retryAfter, Object body, Throwable cause) {
        super(message, statusCode, requestId, retryAfter, body, cause);
    }
}
