package com.likyly;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.likyly.FakeTransport.Step;
import com.likyly.error.ApiException;
import com.likyly.error.AuthenticationException;
import com.likyly.error.LikylyException;
import com.likyly.error.NetworkException;
import com.likyly.error.RateLimitException;
import com.likyly.error.TimeoutException;
import com.likyly.error.ValidationException;
import com.likyly.model.EventInput;
import com.likyly.model.ItemImport;
import com.likyly.model.ItemInput;
import com.likyly.model.ListOptions;
import com.likyly.model.RecommendationRequest;
import com.likyly.model.SessionOptions;
import com.likyly.model.SimilarOptions;
import com.likyly.model.TypedEventInput;
import java.io.IOException;
import java.net.http.HttpTimeoutException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class BehaviorTest {
    static final ObjectMapper MAPPER = new ObjectMapper();
    static final String REC = "{\"recommendation_id\":\"rec_1\",\"strategy\":\"popular\",\"items\":[]}";
    static final String EVT = "{\"message\":\"ok\",\"event_id\":null,\"duplicate\":false}";
    static final String ITEM = "{\"item_id\":\"a\",\"title\":\"t\"}";

    record Harness(Likyly client, FakeTransport transport, List<Long> sleeps) {
    }

    static Harness harness(int maxRetries, Step... steps) {
        FakeTransport transport = new FakeTransport(List.of(steps));
        List<Long> sleeps = new ArrayList<>();
        Likyly client = Likyly.builder().apiKey("sk_test").baseUrl("https://api.example.test").transport(transport)
            .maxRetries(maxRetries).sleeper(sleeps::add).random(() -> 1.0).build();
        return new Harness(client, transport, sleeps);
    }

    static Harness harness(Step... steps) {
        return harness(2, steps);
    }

    // ---- configuration

    @Test
    void requiresAnApiKey() {
        assertThrows(ValidationException.class, () -> Likyly.builder().apiKey(" ").build());
        assertThrows(ValidationException.class, () -> Likyly.builder().build());
    }

    @Test
    void defaultsToTheProductionApiAndAppendsAUserAgent() {
        FakeTransport transport = new FakeTransport(List.of(Step.ok("[]")));
        Likyly.builder().apiKey("k").userAgent("my-shop/1.4").transport(transport).build().items().list();
        assertEquals("api.likyly.com", transport.calls.get(0).uri().getHost());
        assertTrue(transport.calls.get(0).headers().get("User-Agent").matches("^likyly-java/\\d+\\.\\d+\\.\\d+ my-shop/1\\.4$"));
    }

    // ---- validation before sending

    @Test
    void anEventNeedsAnItemAndAUserOrASession() {
        Harness h = harness(Step.ok(EVT));
        assertThrows(ValidationException.class, () -> h.client.events().view(EventInput.builder().itemId("a").build()));
        assertThrows(ValidationException.class, () -> h.client.events().view(EventInput.builder().itemId("").userId("u").build()));
        assertEquals(0, h.transport.calls.size());
    }

    @Test
    void eventTypesAreOpenButUrlSafe() {
        Harness h = harness(Step.ok(EVT));
        h.client.events().track("favorite", EventInput.builder().itemId("i").userId("u").build());
        assertThrows(ValidationException.class, () -> h.client.events().track("bad type!", EventInput.builder().itemId("i").userId("u").build()));
    }

    @Test
    void advancedEndpointsRefuseAnIdContainingASlash() {
        Harness h = harness(Step.ok(REC));
        ValidationException e = assertThrows(ValidationException.class,
            () -> h.client.recommendations().similar(SimilarOptions.builder().itemId("gid://shopify/Product/1").build()));
        assertTrue(e.getMessage().contains("recommendations().get()"));
        h.client.recommendations().get(RecommendationRequest.builder().itemId("gid://shopify/Product/1").build());
        assertEquals(1, h.transport.calls.size());
    }

    @Test
    void sessionNeedsExactlyOneOfListOrUser() {
        Harness h = harness(Step.ok(REC));
        assertThrows(ValidationException.class, () -> h.client.recommendations().session(SessionOptions.builder().build()));
        assertThrows(ValidationException.class, () -> h.client.recommendations().session(SessionOptions.builder().viewedItemIds(List.of("a")).userId("u").build()));
        assertThrows(ValidationException.class, () -> h.client.recommendations().session(SessionOptions.builder().viewedItemIds(List.of()).build()));
    }

    @Test
    void batchesMustNotBeEmpty() {
        Harness h = harness(Step.ok(EVT));
        assertThrows(ValidationException.class, () -> h.client.items().upsertMany(List.of()));
        assertThrows(ValidationException.class, () -> h.client.events().trackMany(List.of()));
    }

    @Test
    void anInstantIsSerializedToIso8601() throws IOException {
        Harness h = harness(Step.ok(EVT));
        h.client.events().view(EventInput.builder().itemId("i").userId("u").occurredAt(Instant.parse("2026-09-24T10:30:00Z")).build());
        assertEquals("2026-09-24T10:30:00Z", MAPPER.readTree(h.transport.calls.get(0).body()).get("occurred_at").asText());
    }

    @Test
    void propertiesKeysAreNeverRenamedAndEmptyPropertiesStayAnObject() throws IOException {
        Harness h = harness(Step.ok(EVT), Step.ok(ITEM));
        h.client.events().purchase(EventInput.builder().itemId("i").userId("u").properties(Map.of("orderId", "O-1", "nested", Map.of("snakeCase_and_camelCase", 1))).build());
        JsonNode props = MAPPER.readTree(h.transport.calls.get(0).body()).get("properties");
        assertEquals("O-1", props.get("orderId").asText());
        assertEquals(1, props.get("nested").get("snakeCase_and_camelCase").asInt());
        h.client.items().upsert("a", new ItemInput("t", null, Map.of()));
        assertTrue(h.transport.calls.get(1).body().contains("\"properties\":{}"));
    }

    // ---- errors

    @Test
    void theHierarchyIsUsable() {
        Harness h = harness(0, Step.status(401, "{\"detail\":\"nope\",\"request_id\":\"req_x\"}"));
        AuthenticationException e = assertThrows(AuthenticationException.class, () -> h.client.items().get("a"));
        assertInstanceOf(ApiException.class, e);
        assertInstanceOf(LikylyException.class, e);
        assertInstanceOf(RuntimeException.class, e);
        assertEquals("req_x", e.requestId());
    }

    @Test
    void a422ListsTheOffendingFields() {
        Harness h = harness(0, Step.status(422, "{\"detail\":[{\"loc\":[\"body\",\"title\"],\"msg\":\"Field required\"}]}"));
        ValidationException e = assertThrows(ValidationException.class, () -> h.client.items().get("a"));
        assertTrue(e.getMessage().contains("Field required"));
    }

    @Test
    void aNonJsonErrorBodyStillBecomesAnApiException() {
        Harness h = harness(0, Step.status(502, "<html>Bad gateway</html>"));
        ApiException e = assertThrows(ApiException.class, () -> h.client.items().get("a"));
        assertEquals(502, e.statusCode());
    }

    @Test
    void networkFailureAndTimeoutAreDistinguished() {
        assertThrows(NetworkException.class, () -> harness(0, Step.fail(new IOException("reset"))).client.items().get("a"));
        assertThrows(TimeoutException.class, () -> harness(0, Step.fail(new HttpTimeoutException("slow"))).client.items().get("a"));
    }

    // ---- retries

    @Test
    void a429IsRetriedForEveryRequestHonoringRetryAfter() {
        Harness h = harness(Step.status(429, Map.of("retry-after", "3"), "{\"detail\":\"slow\"}"), Step.ok(EVT));
        assertFalse(h.client.events().view(EventInput.builder().itemId("i").userId("u").build()).duplicate()); // no eventId: still safe - 429 never reached the app
        assertEquals(2, h.transport.calls.size());
        assertEquals(List.of(3000L), h.sleeps);
    }

    @Test
    void aRetryAfterBeyondAMinuteIsNotWaitedFor() {
        Harness h = harness(Step.status(429, Map.of("retry-after", "600"), "{\"detail\":\"x\"}"));
        RateLimitException e = assertThrows(RateLimitException.class, () -> h.client.items().get("a"));
        assertEquals(600.0, e.retryAfter());
        assertEquals(1, h.transport.calls.size());
    }

    @Test
    void exponentialBackoffWithJitterThenGivesUp() {
        Harness h = harness(3, Step.status(503, "{\"detail\":\"down\"}"));
        assertThrows(ApiException.class, () -> h.client.items().get("a"));
        assertEquals(4, h.transport.calls.size());
        assertEquals(List.of(500L, 1000L, 2000L), h.sleeps);
    }

    @ParameterizedTest
    @ValueSource(ints = {502, 503, 504})
    void idempotentCallsRetryOnGatewayErrors(int status) {
        Harness h = harness(Step.status(status, "{\"detail\":\"x\"}"), Step.ok(ITEM));
        h.client.items().upsert("a", ItemInput.of("t"));
        assertEquals(2, h.transport.calls.size());
    }

    @Test
    void anEventWithoutEventIdIsNeverRetriedOnAnAmbiguousFailure() {
        for (Step first : List.of(Step.status(503, "{\"detail\":\"x\"}"), Step.fail(new IOException("reset")), Step.fail(new HttpTimeoutException("slow")))) {
            Harness h = harness(first, Step.ok(EVT));
            assertThrows(LikylyException.class, () -> h.client.events().purchase(EventInput.builder().itemId("i").userId("u").build()));
            assertEquals(1, h.transport.calls.size());
        }
    }

    @Test
    void anEventWithEventIdIsRetriedTheReplayIsHarmless() {
        Harness h = harness(Step.status(503, "{\"detail\":\"x\"}"), Step.ok("{\"message\":\"dup\",\"event_id\":\"e1\",\"duplicate\":true}"));
        assertTrue(h.client.events().purchase(EventInput.builder().itemId("i").userId("u").eventId("e1").build()).duplicate());
        assertEquals(2, h.transport.calls.size());
    }

    @Test
    void trackManyIsRetriedOnlyIfEveryEventHasAnEventId() {
        String batch = "{\"received\":2,\"accepted\":2,\"duplicates\":0}";
        Harness a = harness(Step.status(503, "{\"detail\":\"x\"}"), Step.ok(batch));
        assertThrows(ApiException.class, () -> a.client.events().trackMany(List.of(
            TypedEventInput.of("view", EventInput.builder().itemId("1").userId("u").build()),
            TypedEventInput.of("view", EventInput.builder().itemId("2").userId("u").eventId("e").build()))));
        assertEquals(1, a.transport.calls.size());
        Harness b = harness(Step.status(503, "{\"detail\":\"x\"}"), Step.ok(batch));
        b.client.events().trackMany(List.of(
            TypedEventInput.of("view", EventInput.builder().itemId("1").userId("u").eventId("a").build()),
            TypedEventInput.of("view", EventInput.builder().itemId("2").userId("u").eventId("b").build())));
        assertEquals(2, b.transport.calls.size());
    }

    @ParameterizedTest
    @ValueSource(ints = {400, 401, 403, 404, 422})
    void clientErrorsAreNeverRetried(int status) {
        Harness h = harness(Step.status(status, "{\"detail\":\"x\"}"), Step.ok("{}"));
        assertThrows(LikylyException.class, () -> h.client.items().get("a"));
        assertEquals(1, h.transport.calls.size());
    }

    // ---- lists

    @Test
    void listsReportTheTotalAndOnlySendWhatWasAskedFor() {
        Harness h = harness(Step.status(200, Map.of("x-total-count", "42"), "[" + ITEM + "]"), Step.ok("[]"));
        var page = h.client.items().list(ListOptions.limit(1));
        assertEquals(42L, page.total());
        assertEquals(1, page.limit());
        h.client.items().list();
        assertEquals(null, h.transport.calls.get(1).uri().getRawQuery());
    }

    @Test
    void importItemsIsAnAliasOfUpsertMany() {
        Harness h = harness(Step.ok("{\"received\":1,\"succeeded\":1,\"failed\":0}"));
        h.client.items().importItems(List.of(new ItemImport("a", "A", null, null)));
        assertEquals("/items/import", h.transport.calls.get(0).uri().getRawPath());
    }
}
