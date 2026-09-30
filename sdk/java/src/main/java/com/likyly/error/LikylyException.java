package com.likyly.error;

/**
 * Every exception the SDK throws extends LikylyException (unchecked):
 *
 * <pre>
 * LikylyException
 * ├─ NetworkException             no HTTP response (DNS, connection reset, ...)
 * ├─ TimeoutException             the request timed out
 * ├─ ValidationException          invalid request - caught by the SDK before sending, or 422 from the API
 * └─ ApiException                 the API answered with an error status
 *    ├─ AuthenticationException   401 - missing / invalid / revoked API key
 *    ├─ PermissionDeniedException 403 - e.g. a public key used for a secret-key operation, or a plan limit
 *    ├─ NotFoundException         404
 *    └─ RateLimitException        429 - see {@link #retryAfter()}
 * </pre>
 */
public class LikylyException extends RuntimeException {
    private static final long serialVersionUID = 1L;

    private final Integer statusCode;
    private final String requestId;
    private final Double retryAfter;
    private final transient Object body;

    public LikylyException(String message) {
        this(message, null, null, null, null, null);
    }

    public LikylyException(String message, Integer statusCode, String requestId, Double retryAfter, Object body, Throwable cause) {
        super(message, cause);
        this.statusCode = statusCode;
        this.requestId = requestId;
        this.retryAfter = retryAfter;
        this.body = body;
    }

    /** HTTP status code, when there was an HTTP response (null otherwise). */
    public Integer statusCode() {
        return statusCode;
    }

    /** The {@code request_id} of the failed call (also the {@code X-Request-ID} header) - quote it when reporting a problem. */
    public String requestId() {
        return requestId;
    }

    /** Seconds to wait before retrying, from the {@code Retry-After} header (429/503); null if absent. */
    public Double retryAfter() {
        return retryAfter;
    }

    /** The parsed response body (a Map, List or String), when there was one. */
    public Object body() {
        return body;
    }
}
