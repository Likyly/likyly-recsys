package com.likyly;

import com.likyly.error.ValidationException;
import com.likyly.http.JdkTransport;
import com.likyly.http.Transport;
import com.likyly.internal.HttpCore;
import com.likyly.resources.EventsResource;
import com.likyly.resources.ItemsResource;
import com.likyly.resources.RecommendationsResource;
import com.likyly.resources.UsersResource;
import java.time.Duration;
import java.util.function.DoubleSupplier;
import java.util.function.LongConsumer;

/**
 * The LIKYLY client. Four things to know:
 *
 * <pre>
 * likyly.items()            your catalog
 * likyly.users()            your users (optional)
 * likyly.events()           what visitors do
 * likyly.recommendations()  what to show them
 * </pre>
 *
 * <p>Two API keys: the <b>secret</b> key (backend only: items, users, everything) and the <b>public</b> key
 * (recommendations and event tracking only). Never put the secret key in code that ships to a browser.
 *
 * <p>Thread-safe: build one instance and share it (e.g. as a Spring bean).
 */
public final class Likyly {
    public static final String VERSION = "1.0.0";
    public static final String DEFAULT_BASE_URL = "https://api.likyly.com";

    private final ItemsResource items;
    private final UsersResource users;
    private final EventsResource events;
    private final RecommendationsResource recommendations;

    private Likyly(Builder b) {
        if (b.apiKey == null || b.apiKey.isBlank()) {
            throw new ValidationException("apiKey is required (create one in your LIKYLY account)");
        }
        HttpCore http = new HttpCore(
            b.transport != null ? b.transport : new JdkTransport(),
            b.apiKey,
            b.baseUrl.replaceAll("/+$", ""),
            b.catalog,
            b.timeout,
            b.maxRetries,
            b.userAgent != null ? "likyly-java/" + VERSION + " " + b.userAgent : "likyly-java/" + VERSION,
            b.sleeper != null ? b.sleeper : millis -> {
                try {
                    Thread.sleep(millis);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            },
            b.random != null ? b.random : Math::random);
        this.items = new ItemsResource(http);
        this.users = new UsersResource(http);
        this.events = new EventsResource(http);
        this.recommendations = new RecommendationsResource(http);
    }

    public static Builder builder() {
        return new Builder();
    }

    public ItemsResource items() {
        return items;
    }

    public UsersResource users() {
        return users;
    }

    public EventsResource events() {
        return events;
    }

    public RecommendationsResource recommendations() {
        return recommendations;
    }

    /** Client configuration. Only {@link #apiKey} is required. */
    public static final class Builder {
        private String apiKey;
        private String baseUrl = DEFAULT_BASE_URL;
        private String catalog;
        private Duration timeout = Duration.ofSeconds(10);
        private int maxRetries = 2;
        private String userAgent;
        private Transport transport;
        private LongConsumer sleeper;
        private DoubleSupplier random;

        /** Your API key (secret for backend use, public for browser-facing use). */
        public Builder apiKey(String apiKey) {
            this.apiKey = apiKey;
            return this;
        }

        /** Defaults to {@code https://api.likyly.com}. */
        public Builder baseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
            return this;
        }

        /** Which of your catalogs to use, if your account has several (omit it if you have one). */
        public Builder catalog(String catalog) {
            this.catalog = catalog;
            return this;
        }

        /** Per-request timeout. Default 10 s. */
        public Builder timeout(Duration timeout) {
            this.timeout = timeout;
            return this;
        }

        /** Automatic retries on transient failures - 429, 502, 503, 504, dropped connections. Default 2; 0 disables. */
        public Builder maxRetries(int maxRetries) {
            this.maxRetries = maxRetries;
            return this;
        }

        /** Appended to the SDK's User-Agent, e.g. {@code my-shop/1.4}. */
        public Builder userAgent(String userAgent) {
            this.userAgent = userAgent;
            return this;
        }

        /** Your own {@link java.net.http.HttpClient} (proxy, executor, TLS settings). */
        public Builder httpClient(java.net.http.HttpClient client) {
            this.transport = new JdkTransport(client);
            return this;
        }

        /** A completely custom HTTP stack. */
        public Builder transport(Transport transport) {
            this.transport = transport;
            return this;
        }

        /** Test hook: replaces {@code Thread.sleep} between retries. */
        Builder sleeper(LongConsumer sleeper) {
            this.sleeper = sleeper;
            return this;
        }

        /** Test hook: replaces the jitter source. */
        Builder random(DoubleSupplier random) {
            this.random = random;
            return this;
        }

        public Likyly build() {
            return new Likyly(this);
        }
    }
}
