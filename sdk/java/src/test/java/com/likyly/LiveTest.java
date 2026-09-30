package com.likyly;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.likyly.error.AuthenticationException;
import com.likyly.error.NotFoundException;
import com.likyly.error.PermissionDeniedException;
import com.likyly.error.ValidationException;
import com.likyly.model.EventInput;
import com.likyly.model.ItemImport;
import com.likyly.model.ItemInput;
import com.likyly.model.ListOptions;
import com.likyly.model.RecommendationRequest;
import com.likyly.model.RecommendationResponse;
import com.likyly.model.SessionOptions;
import com.likyly.model.SimilarOptions;
import com.likyly.model.TypedEventInput;
import com.likyly.model.UserInput;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;

/** End-to-end against a REAL LIKYLY API (sdk/conformance/local-api.sh starts one). Skipped unless LIKYLY_TEST_* are set. */
class LiveTest {
    static final String GID = "gid://shopify/Product/123456";

    @Test
    void fullWorkflow() {
        String url = System.getenv("LIKYLY_TEST_URL");
        String secret = System.getenv("LIKYLY_TEST_SECRET_KEY");
        String pub = System.getenv("LIKYLY_TEST_PUBLIC_KEY");
        Assumptions.assumeTrue(url != null && !url.isBlank() && secret != null && pub != null, "LIKYLY_TEST_* not set");
        String catalog = "java-" + System.currentTimeMillis();
        Likyly server = Likyly.builder().apiKey(secret).baseUrl(url).catalog(catalog).build();
        Likyly browser = Likyly.builder().apiKey(pub).baseUrl(url).catalog(catalog).build();

        var created = server.items().upsert("SKU-123", new ItemInput("Nike Air Max", "Running shoe", Map.of("category", "shoes", "price", 129.9)));
        assertEquals("SKU-123", created.itemId());
        assertEquals(129.9, ((Number) created.properties().get("price")).doubleValue());
        server.items().upsert(GID, new ItemInput("Shopify boot", "warm winter boot", Map.of("category", "boots")));
        assertEquals("Shopify boot", server.items().get(GID).title());
        var page = server.items().list(ListOptions.limit(1));
        assertEquals(1, page.items().size());
        assertEquals(2L, page.total());
        assertEquals(1, server.items().upsertMany(List.of(new ItemImport("SKU-A", "Adidas", "road running shoe", null))).succeeded());
        server.items().delete("SKU-A");
        assertThrows(NotFoundException.class, () -> server.items().get("SKU-A"));

        assertEquals("premium", server.users().upsert("user_123", new UserInput(Map.of("country", "FR", "segment", "premium"))).properties().get("segment"));
        assertFalse(server.users().list(ListOptions.limit(10)).users().isEmpty());

        browser.events().view(EventInput.builder().itemId("SKU-123").userId("user_123").build());
        browser.events().view(EventInput.builder().itemId("SKU-123").sessionId("sess_123").build());
        browser.events().track("favorite", EventInput.builder().itemId("SKU-123").userId("user_123").build());
        EventInput purchase = EventInput.builder().itemId("SKU-123").userId("user_123").eventId("purchase_" + System.nanoTime())
            .properties(Map.of("orderId", "O-1")).build();
        assertFalse(browser.events().purchase(purchase).duplicate());
        assertTrue(browser.events().purchase(purchase).duplicate());
        assertEquals(1, browser.events().trackMany(List.of(TypedEventInput.of("view", EventInput.builder().itemId(GID).userId("user_123").build()))).accepted());

        RecommendationResponse rec = browser.recommendations().get(RecommendationRequest.builder().userId("user_123").placement("homepage").limit(2).build());
        assertTrue(rec.recommendationId().startsWith("rec_"));
        assertEquals("homepage", rec.placement());
        assertFalse(rec.items().isEmpty());
        assertEquals("content", browser.recommendations().get(RecommendationRequest.builder().itemId("SKU-123").limit(2).build()).strategy());
        assertEquals("content", browser.recommendations().get(RecommendationRequest.builder().itemId(GID).limit(2).build()).strategy());
        browser.events().impression(EventInput.builder().itemId(rec.items().get(0).itemId()).userId("user_123").recommendationId(rec.recommendationId()).placement("homepage").build());
        assertEquals("content", browser.recommendations().similar(SimilarOptions.builder().itemId("SKU-123").limit(2).build()).strategy());
        assertEquals("session", browser.recommendations().session(SessionOptions.builder().viewedItemIds(List.of("SKU-123")).limit(2).build()).strategy());
        assertFalse(browser.recommendations().popular().recommendationId().isEmpty());

        assertThrows(PermissionDeniedException.class, () -> browser.items().upsert("x", ItemInput.of("t")));
        assertThrows(AuthenticationException.class, () -> Likyly.builder().apiKey("nope").baseUrl(url).build().events().view(EventInput.builder().itemId("i").userId("u").build()));
        ValidationException e = assertThrows(ValidationException.class, () -> server.items().list(ListOptions.limit(5000)));
        assertTrue(e.requestId().startsWith("req_"));

        server.items().deleteMany(List.of("SKU-123", GID));
        server.users().delete("user_123");
    }
}
