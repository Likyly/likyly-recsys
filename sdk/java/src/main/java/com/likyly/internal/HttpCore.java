package com.likyly.internal;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.likyly.error.Errors;
import com.likyly.error.LikylyException;
import com.likyly.error.NetworkException;
import com.likyly.error.TimeoutException;
import com.likyly.http.Transport;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.DoubleSupplier;
import java.util.function.LongConsumer;

/**
 * The only place that talks HTTP. Every resource goes through {@link #request}: auth header, catalog
 * parameter, timeout, retries with exponential backoff + jitter, and the error mapping.
 */
public final class HttpCore {
    static final ObjectMapper MAPPER = new ObjectMapper();
    private static final double RETRY_BASE_MS = 500;
    private static final double RETRY_CAP_MS = 8_000;
    /** A Retry-After longer than this is not waited for: the RateLimitException is thrown instead. */
    private static final double MAX_RETRY_AFTER_S = 60;

    private final Transport transport;
    private final String apiKey;
    private final String baseUrl;
    private final String catalog;
    private final Duration timeout;
    private final int maxRetries;
    private final String userAgent;
    private final LongConsumer sleeper;
    private final DoubleSupplier random;

    public HttpCore(Transport transport, String apiKey, String baseUrl, String catalog, Duration timeout, int maxRetries,
                    String userAgent, LongConsumer sleeper, DoubleSupplier random) {
        this.transport = transport;
        this.apiKey = apiKey;
        this.baseUrl = baseUrl;
        this.catalog = catalog;
        this.timeout = timeout;
        this.maxRetries = maxRetries;
        this.userAgent = userAgent;
        this.sleeper = sleeper;
        this.random = random;
    }

    /** A parsed successful response. */
    public record Result(int status, Map<String, String> headers, JsonNode data) {
    }

    /**
     * @param idempotent safe to send again if the outcome is unknown (a timeout, a 5xx, a dropped connection)?
     *                   false for events without an eventId, where a blind retry could record the event twice
     */
    public Result request(String method, String path, Map<String, String> query, ObjectNode body, boolean idempotent) {
        URI uri = buildUri(path, query);
        int attempt = 0;
        while (true) {
            try {
                return once(method, uri, body);
            } catch (LikylyException error) {
                Long delayMs = retryDelay(error, attempt, idempotent);
                if (delayMs == null || attempt >= maxRetries) {
                    throw error;
                }
                attempt++;
                sleeper.accept(delayMs);
            }
        }
    }

    private Long retryDelay(LikylyException error, int attempt, boolean idempotent) {
        long backoff = (long) (random.getAsDouble() * Math.min(RETRY_CAP_MS, RETRY_BASE_MS * Math.pow(2, attempt))); // full jitter
        Integer status = error.statusCode();
        if (status != null && status == 429) {
            // Rejected by the rate limiter before reaching the application: nothing was processed, so retrying is safe for every request.
            return error.retryAfter() != null ? afterHeader(error.retryAfter()) : Long.valueOf(backoff);
        }
        if (!idempotent) {
            return null; // the outcome is unknown - never risk a duplicate
        }
        if (status != null && (status == 502 || status == 503 || status == 504)) {
            return error.retryAfter() != null ? afterHeader(error.retryAfter()) : Long.valueOf(backoff);
        }
        if (error instanceof NetworkException || error instanceof TimeoutException) {
            return backoff;
        }
        return null;
    }

    private static Long afterHeader(double seconds) {
        return seconds <= MAX_RETRY_AFTER_S ? Long.valueOf((long) (seconds * 1000)) : null;
    }

    private URI buildUri(String path, Map<String, String> query) {
        Map<String, String> params = new LinkedHashMap<>(query);
        if (catalog != null && !catalog.isEmpty()) {
            params.put("data_product_type", catalog);
        }
        StringBuilder url = new StringBuilder(baseUrl).append(path);
        char separator = '?';
        for (Map.Entry<String, String> e : params.entrySet()) {
            if (e.getValue() == null) {
                continue;
            }
            url.append(separator).append(encode(e.getKey())).append('=').append(encode(e.getValue()));
            separator = '&';
        }
        return URI.create(url.toString());
    }

    private Result once(String method, URI uri, ObjectNode body) {
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("X-API-Key", apiKey);
        headers.put("Accept", "application/json");
        headers.put("User-Agent", userAgent);
        String payload = null;
        if (body != null) {
            headers.put("Content-Type", "application/json");
            payload = body.toString();
        }

        Transport.Response response;
        try {
            response = transport.send(new Transport.Request(method, uri, headers, payload), timeout);
        } catch (HttpTimeoutException e) {
            throw new TimeoutException("LIKYLY request timed out after " + timeout.toMillis() + " ms", null, null, null, null, e);
        } catch (IOException e) {
            throw new NetworkException("Could not reach the LIKYLY API: " + e.getMessage(), null, null, null, null, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new NetworkException("Interrupted while calling the LIKYLY API", null, null, null, null, e);
        }

        Object parsed = null;
        JsonNode node = null;
        String text = response.body();
        if (text != null && !text.isEmpty()) {
            try {
                node = MAPPER.readTree(text);
                parsed = MAPPER.treeToValue(node, Object.class);
            } catch (JsonProcessingException e) {
                parsed = text; // e.g. an HTML 502 page from a proxy
            }
        }
        if (response.status() >= 400) {
            String requestId = response.headers().get("x-request-id");
            if (requestId == null && parsed instanceof Map<?, ?> m && m.get("request_id") != null) {
                requestId = String.valueOf(m.get("request_id"));
            }
            throw Errors.fromResponse(response.status(), parsed, requestId, parseRetryAfter(response.headers().get("retry-after")));
        }
        return new Result(response.status(), response.headers(), node);
    }

    private static Double parseRetryAfter(String value) {
        if (value == null || value.isEmpty()) {
            return null;
        }
        try {
            double seconds = Double.parseDouble(value);
            return seconds >= 0 ? seconds : null;
        } catch (NumberFormatException ignored) {
            // fall through: an HTTP date
        }
        try {
            long ms = ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant().toEpochMilli() - System.currentTimeMillis();
            return Math.max(0, ms / 1000.0);
        } catch (RuntimeException e) {
            return null;
        }
    }

    /** Percent-encodes everything outside RFC 3986 unreserved (A-Z a-z 0-9 - . _ ~), '/' included. */
    public static String encode(String value) {
        StringBuilder out = new StringBuilder();
        for (byte b : value.getBytes(StandardCharsets.UTF_8)) {
            int c = b & 0xFF;
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '_' || c == '~') {
                out.append((char) c);
            } else {
                out.append('%').append(String.format("%02X", c));
            }
        }
        return out.toString();
    }

    public static ObjectMapper mapper() {
        return MAPPER;
    }
}
