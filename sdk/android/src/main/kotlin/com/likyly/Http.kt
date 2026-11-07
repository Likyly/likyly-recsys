package com.likyly

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runInterruptible
import kotlinx.coroutines.withTimeoutOrNull
import java.io.IOException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.URLEncoder
import java.text.SimpleDateFormat
import java.util.Locale

/** One HTTP request, as the SDK hands it to a [LikylyTransport]. */
public class HttpRequest(
    public val method: String,
    public val url: String,
    public val headers: Map<String, String>,
    public val body: ByteArray?,
    public val timeoutMillis: Long,
)

/** One HTTP response. Header names are lower-cased. */
public class HttpResponse(
    public val status: Int,
    public val headers: Map<String, String>,
    public val body: ByteArray,
)

/**
 * What the SDK needs from an HTTP stack. The default is [UrlConnectionTransport] (java.net.HttpURLConnection,
 * no dependency); plug your own to go through OkHttp, a proxy, or a test double. Throw an IOException when there
 * is no HTTP response (a [SocketTimeoutException] for a timeout).
 */
public fun interface LikylyTransport {
    public suspend fun send(request: HttpRequest): HttpResponse
}

/** The default transport: java.net.HttpURLConnection on Dispatchers.IO. */
public object UrlConnectionTransport : LikylyTransport {
    override suspend fun send(request: HttpRequest): HttpResponse = runInterruptible(Dispatchers.IO) {
        val conn = URL(request.url).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = request.method
            conn.connectTimeout = request.timeoutMillis.toInt()
            conn.readTimeout = request.timeoutMillis.toInt()
            conn.useCaches = false
            conn.instanceFollowRedirects = true
            for ((k, v) in request.headers) conn.setRequestProperty(k, v)
            if (request.body != null) {
                conn.doOutput = true
                conn.setFixedLengthStreamingMode(request.body.size)
                conn.outputStream.use { it.write(request.body) }
            }
            val status = conn.responseCode
            val stream = if (status >= 400) conn.errorStream else conn.inputStream
            val bytes = stream?.use { it.readBytes() } ?: ByteArray(0)
            val headers = HashMap<String, String>()
            for ((k, v) in conn.headerFields) if (k != null) headers[k.lowercase(Locale.ROOT)] = v.joinToString(", ")
            HttpResponse(status, headers, bytes)
        } finally {
            conn.disconnect()
        }
    }
}

internal class Call(
    val method: String,
    val path: String,
    val query: List<Pair<String, String>> = emptyList(),
    val body: Map<String, Any?>? = null,
    /**
     * Whether the call is safe to send again if its outcome is unknown (a timeout, a 5xx, a dropped connection):
     * false for events without an eventId, where a blind retry could record the event twice.
     */
    val idempotent: Boolean,
)

/** The only place that talks HTTP: auth header, catalog parameter, timeout, retries with backoff, error mapping. */
internal class Core(
    private val apiKey: String,
    baseUrl: String,
    private val catalog: String?,
    private val timeoutMillis: Long,
    private val maxRetries: Int,
    private val userAgent: String,
    private val transport: LikylyTransport,
    private val sleep: suspend (Long) -> Unit,
    private val random: () -> Double,
) {
    private val baseUrl = baseUrl.trimEnd('/')

    suspend fun request(call: Call): Map<String, Any?> {
        val url = buildUrl(call.path, call.query)
        val payload = call.body?.let { Json.encode(it).toByteArray(Charsets.UTF_8) }
        var attempt = 0
        while (true) {
            try {
                return once(call.method, url, payload)
            } catch (e: LikylyException) {
                val delay = retryDelay(e, attempt, call.idempotent)
                if (delay == null || attempt >= maxRetries) throw e
                sleep(delay)
                attempt++
            }
        }
    }

    /** Milliseconds to wait before the next attempt, or null when the failure must not be retried. */
    internal fun retryDelay(e: LikylyException, attempt: Int, idempotent: Boolean): Long? {
        val backoff = (random() * minOf(RETRY_CAP_MS.toDouble(), RETRY_BASE_MS * Math.pow(2.0, attempt.toDouble()))).toLong()
        if (e is ApiException) {
            val afterHeader: () -> Long? = {
                val ra = e.retryAfterSeconds
                if (ra == null) backoff else if (ra * 1000 <= MAX_RETRY_AFTER_MS) (ra * 1000).toLong() else null
            }
            // Rejected by the rate limiter before reaching the application: nothing was processed, so it is always safe.
            if (e.statusCode == 429) return afterHeader()
            if (!idempotent) return null // the outcome is unknown - never risk a duplicate
            if (e.statusCode == 502 || e.statusCode == 503 || e.statusCode == 504) return afterHeader()
            return null
        }
        if (!idempotent) return null
        if (e is NetworkException || e is TimeoutException) return backoff
        return null
    }

    private fun buildUrl(path: String, query: List<Pair<String, String>>): String {
        val params = if (catalog.isNullOrEmpty()) query else query + ("data_product_type" to catalog)
        val sb = StringBuilder(baseUrl).append(path)
        params.forEachIndexed { index, (k, v) ->
            sb.append(if (index == 0) '?' else '&').append(queryEscape(k)).append('=').append(queryEscape(v))
        }
        return sb.toString()
    }

    private suspend fun once(method: String, url: String, payload: ByteArray?): Map<String, Any?> {
        val headers = linkedMapOf(
            "X-API-Key" to apiKey,
            "Accept" to "application/json",
            "User-Agent" to userAgent,
        )
        if (payload != null) headers["Content-Type"] = "application/json"
        val request = HttpRequest(method, url, headers, payload, timeoutMillis)
        val response = try {
            // A safety net over the transport's own timeouts (a custom transport may have none).
            withTimeoutOrNull(timeoutMillis + 1000) { transport.send(request) } ?: throw TimeoutException(timeoutMillis)
        } catch (e: LikylyException) {
            throw e
        } catch (e: CancellationException) {
            throw e
        } catch (e: SocketTimeoutException) {
            throw TimeoutException(timeoutMillis, e)
        } catch (e: IOException) {
            throw NetworkException("could not reach the API: ${e.message ?: e.javaClass.simpleName}", e)
        }
        val headersLower = response.headers.mapKeys { it.key.lowercase(Locale.ROOT) }
        val text = String(response.body, Charsets.UTF_8)
        if (response.status >= 400) {
            val data: Any? = if (text.isEmpty()) null else try { Json.parse(text) } catch (_: IllegalArgumentException) { text }
            val requestId = headersLower["x-request-id"] ?: (data as? Map<*, *>)?.get("request_id")?.toString()
            throw apiError(response.status, data, requestId, parseRetryAfter(headersLower["retry-after"]))
        }
        @Suppress("UNCHECKED_CAST")
        return try {
            Json.parse(text.ifEmpty { "{}" }) as? Map<String, Any?>
        } catch (_: IllegalArgumentException) {
            null
        } ?: throw ApiException("the API answered with a body that is not the expected JSON", response.status)
    }

    internal companion object {
        const val RETRY_BASE_MS = 500.0
        const val RETRY_CAP_MS = 8000L
        /** A Retry-After longer than this is not waited for: the RateLimitException is thrown instead. */
        const val MAX_RETRY_AFTER_MS = 60_000L

        fun parseRetryAfter(value: String?): Double? {
            if (value.isNullOrBlank()) return null
            value.trim().toDoubleOrNull()?.let { return if (it >= 0) it else null }
            return try {
                val fmt = SimpleDateFormat("EEE, dd MMM yyyy HH:mm:ss zzz", Locale.US)
                val date = fmt.parse(value.trim()) ?: return null
                maxOf(0.0, (date.time - System.currentTimeMillis()) / 1000.0)
            } catch (_: java.text.ParseException) {
                null
            }
        }
    }
}

private fun queryEscape(s: String): String = URLEncoder.encode(s, "UTF-8")

/**
 * Percent-encodes a path segment: everything outside RFC 3986 "unreserved" (A-Z a-z 0-9 - . _ ~) becomes %XX
 * (UTF-8), '/' included - so an id like "gid://shopify/Product/1" is one segment.
 */
internal fun encodeSegment(s: String): String {
    val hex = "0123456789ABCDEF"
    val sb = StringBuilder()
    for (b in s.toByteArray(Charsets.UTF_8)) {
        val c = b.toInt() and 0xFF
        val ch = c.toChar()
        if (ch in 'A'..'Z' || ch in 'a'..'z' || ch in '0'..'9' || ch == '-' || ch == '.' || ch == '_' || ch == '~') {
            sb.append(ch)
        } else {
            sb.append('%').append(hex[c shr 4]).append(hex[c and 15])
        }
    }
    return sb.toString()
}

private val EVENT_TYPE = Regex("^[A-Za-z0-9_-]{1,64}$")

/** Ids are opaque, non-empty strings. */
internal fun requireId(value: String?, name: String): String {
    if (value == null || value.isBlank()) {
        throw ValidationException("$name must be a non-empty string (your own identifier, e.g. \"SKU-123\")")
    }
    return value
}

/** The advanced recommendation endpoints carry ids in the URL path, where '/' cannot be used. */
internal fun requirePathSafeId(value: String?, name: String): String {
    val id = requireId(value, name)
    if (id.contains('/')) {
        throw ValidationException(
            "$name \"$id\" contains \"/\", which this endpoint cannot carry in its URL path - use recommendations.get, which takes ids in the request body",
        )
    }
    return id
}

internal fun requireEventType(type: String?): String {
    if (type == null || !EVENT_TYPE.matches(type)) {
        throw ValidationException("event type must be 1-64 characters of letters, digits, \"_\" or \"-\" (e.g. \"view\", \"add_to_cart\", \"favorite\")")
    }
    return type
}
