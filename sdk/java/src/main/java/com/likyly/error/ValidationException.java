package com.likyly.error;

/** Invalid request: caught by the SDK before sending, or a 422 from the API. */
public class ValidationException extends LikylyException {
    private static final long serialVersionUID = 1L;

    public ValidationException(String message) {
        super(message);
    }

    public ValidationException(String message, Integer statusCode, String requestId, Double retryAfter, Object body, Throwable cause) {
        super(message, statusCode, requestId, retryAfter, body, cause);
    }
}
