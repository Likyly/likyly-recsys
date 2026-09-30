package com.likyly.http;

import java.io.IOException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

/** The default {@link Transport}: {@code java.net.http.HttpClient} (JDK 11+, no dependency). */
public final class JdkTransport implements Transport {
    private final HttpClient client;

    public JdkTransport(HttpClient client) {
        this.client = client;
    }

    public JdkTransport() {
        this(HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).build());
    }

    @Override
    public Response send(Request request, Duration timeout) throws IOException, InterruptedException {
        HttpRequest.Builder builder = HttpRequest.newBuilder(request.uri()).timeout(timeout);
        request.headers().forEach(builder::header);
        builder.method(request.method(), request.body() == null
            ? HttpRequest.BodyPublishers.noBody()
            : HttpRequest.BodyPublishers.ofString(request.body()));
        HttpResponse<String> response = client.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        Map<String, String> headers = new HashMap<>();
        response.headers().map().forEach((name, values) -> headers.put(name.toLowerCase(), String.join(", ", values)));
        return new Response(response.statusCode(), headers, response.body());
    }
}
