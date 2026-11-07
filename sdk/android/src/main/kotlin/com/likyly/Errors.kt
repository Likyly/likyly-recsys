package com.likyly

/**
 * Every exception the SDK throws is a [LikylyException]:
 *
 *     ValidationException          invalid request - caught by the SDK before sending, or a 422 from the API
 *     NetworkException             no HTTP response (no connection, DNS, connection reset, ...)
 *     TimeoutException             the request timed out
 *     PausedException              LIKYLY failed moments ago: recommendations are skipped for a short pause
 *     ApiException                 the API answered with an error status
 *       AuthenticationException    401 - missing / invalid / revoked API key
 *       PermissionDeniedException  403 - e.g. a public key used for a secret-key operation, or a plan limit
 *       NotFoundException          404
 *       RateLimitException         429 - see retryAfterSeconds
 *
 * Coroutine cancellation is never wrapped: a cancelled scope gets its CancellationException as usual.
 */
public open class LikylyException(message: String, cause: Throwable? = null) : Exception(message, cause)

/** Invalid request: caught by the SDK before sending ([statusCode] null), or a 422 from the API. */
public class ValidationException(
    message: String,
    public val statusCode: Int? = null,
    public val requestId: String? = null,
    public val body: Any? = null,
) : LikylyException(message)

/** No HTTP response: no connection, DNS failure, connection reset, ... */
public class NetworkException(message: String, cause: Throwable? = null) : LikylyException(message, cause)

/** The request timed out (the client's timeout). */
public class TimeoutException(public val timeoutMillis: Long, cause: Throwable? = null) :
    LikylyException("request timed out after ${timeoutMillis}ms", cause)

/**
 * LIKYLY did not answer a moment ago (network error, timeout, 429 or 5xx), so placement calls are skipped
 * for `pauseSeconds`: show your own fallback content straight away.
 */
public class PausedException(public val retryInSeconds: Long) :
    LikylyException("LIKYLY did not answer a moment ago: calls resume in ${retryInSeconds}s")

/** The API answered with an error status. */
public open class ApiException(
    message: String,
    /** The HTTP status code. */
    public val statusCode: Int,
    /** The request_id of the failed call (also the X-Request-ID header) - quote it when reporting a problem. */
    public val requestId: String? = null,
    /** The Retry-After header in seconds (429/503), null if absent. */
    public val retryAfterSeconds: Double? = null,
    /** The parsed response body (a Map, a List or a String), when there was one. */
    public val body: Any? = null,
) : LikylyException(message)

/** 401: missing, invalid or revoked API key. */
public class AuthenticationException(message: String, statusCode: Int, requestId: String?, retryAfterSeconds: Double?, body: Any?) :
    ApiException(message, statusCode, requestId, retryAfterSeconds, body)

/** 403: e.g. a public key used for a secret-key operation, or a plan limit. */
public class PermissionDeniedException(message: String, statusCode: Int, requestId: String?, retryAfterSeconds: Double?, body: Any?) :
    ApiException(message, statusCode, requestId, retryAfterSeconds, body)

/** 404. */
public class NotFoundException(message: String, statusCode: Int, requestId: String?, retryAfterSeconds: Double?, body: Any?) :
    ApiException(message, statusCode, requestId, retryAfterSeconds, body)

/** 429: [retryAfterSeconds] says how long to wait. */
public class RateLimitException(message: String, statusCode: Int, requestId: String?, retryAfterSeconds: Double?, body: Any?) :
    ApiException(message, statusCode, requestId, retryAfterSeconds, body)

internal fun apiError(status: Int, body: Any?, requestId: String?, retryAfterSeconds: Double?): LikylyException {
    val message = messageOf(body).ifEmpty { "API error (HTTP $status)" }
    return when (status) {
        401 -> AuthenticationException(message, status, requestId, retryAfterSeconds, body)
        403 -> PermissionDeniedException(message, status, requestId, retryAfterSeconds, body)
        404 -> NotFoundException(message, status, requestId, retryAfterSeconds, body)
        422 -> ValidationException(message, status, requestId, body)
        429 -> RateLimitException(message, status, requestId, retryAfterSeconds, body)
        else -> ApiException(message, status, requestId, retryAfterSeconds, body)
    }
}

private fun messageOf(body: Any?): String {
    val detail = (body as? Map<*, *>)?.get("detail") ?: return ""
    return when (detail) {
        is String -> detail
        is List<*> -> detail.joinToString("; ") { item -> ((item as? Map<*, *>)?.get("msg") ?: item).toString() }
        else -> detail.toString()
    }
}

/** The HTTP status of an API failure, or null for anything else. */
internal val LikylyException.httpStatus: Int?
    get() = when (this) {
        is ApiException -> statusCode
        is ValidationException -> statusCode
        else -> null
    }
