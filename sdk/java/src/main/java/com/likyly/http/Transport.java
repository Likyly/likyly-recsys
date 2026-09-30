package com.likyly.http;

import java.io.IOException;
import java.time.Duration;
import java.util.Map;

/**
 * How bytes reach the API. The default implementation uses {@code java.net.http.HttpClient}; supply your
 * own to use another HTTP stack (OkHttp, Apache, a corporate proxy...) or to test without a network.
 *
 * <p>Throw {@link java.net.http.HttpTimeoutException} for a timeout and any other {@link IOException} for a
 * network failure - the SDK maps them to {@code TimeoutException} / {@code NetworkException}.
 */
@FunctionalInterface
public interface Transport {
    Response send(Request request, Duration timeout) throws IOException, InterruptedException;

    /** A request: {@code uri} is complete (path and query already percent-encoded); {@code body} is JSON or null. */
    record Request(String method, java.net.URI uri, Map<String, String> headers, String body) {
    }

    /** A response: header names lower-cased. */
    record Response(int status, Map<String, String> headers, String body) {
    }
}
