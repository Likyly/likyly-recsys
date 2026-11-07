package com.likyly

import kotlinx.coroutines.runBlocking
import java.io.IOException
import java.net.SocketTimeoutException
import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue

class BehaviorTest {
    private val placementBody = mapOf(
        "recommendation_id" to "rec_1", "placement" to "pdp-related", "strategy_used" to "hybrid",
        "items" to listOf(mapOf("item_id" to "SKU-1", "score" to 0.9, "properties" to mapOf("brand" to "Nike"))),
    )
    private val eventBody = mapOf("message" to "ok", "event_id" to null, "duplicate" to false)

    @Test
    fun secretAndDeveloperKeysAreRefused() {
        for (key in listOf("sk_live_123", "lk_dev_123")) {
            val e = assertFailsWith<ValidationException> { Likyly(key) }
            assertTrue(e.message!!.contains("public key"))
        }
        assertFailsWith<ValidationException> { Likyly("  ") }
        Likyly("pk_live_123") // public key accepted
        Likyly("legacy-unprefixed-key") // older keys keep working
    }

    @Test
    fun eventWithoutEventIdIsNotRetriedOn503() = runBlocking {
        val t = TestClient()
        t.transport.reply(503, mapOf("detail" to "down")).reply(200, eventBody)
        val e = assertFailsWith<ApiException> { t.likyly.events.view(EventInput(itemId = "SKU-1", userId = "u")) }
        assertEquals(503, e.statusCode)
        assertEquals(1, t.transport.requests.size)
    }

    @Test
    fun eventWithoutEventIdIsNotRetriedOnNetworkError() = runBlocking {
        val t = TestClient()
        t.transport.fail(IOException("reset")).reply(200, eventBody)
        assertFailsWith<NetworkException> { t.likyly.events.click(EventInput(itemId = "SKU-1", userId = "u")) }
        assertEquals(1, t.transport.requests.size)
    }

    @Test
    fun eventWithEventIdIsRetried() = runBlocking {
        val t = TestClient()
        t.transport.reply(503, null).fail(SocketTimeoutException("slow")).reply(200, eventBody)
        t.likyly.events.purchase(EventInput(itemId = "SKU-1", userId = "u", eventId = "purchase_1_SKU-1"))
        assertEquals(3, t.transport.requests.size)
        assertEquals(2, t.sleeps.size)
    }

    @Test
    fun rateLimitIsAlwaysRetriedHonoringRetryAfter() = runBlocking {
        val t = TestClient()
        t.transport.reply(429, mapOf("detail" to "slow down"), mapOf("Retry-After" to "3")).reply(200, eventBody)
        t.likyly.events.view(EventInput(itemId = "SKU-1", sessionId = "s")) // no eventId: 429 is still safe
        assertEquals(listOf(3000L), t.sleeps)
        assertEquals(2, t.transport.requests.size)
    }

    @Test
    fun tooLongRetryAfterIsNotWaitedFor() = runBlocking {
        val t = TestClient()
        t.transport.reply(429, mapOf("detail" to "slow down"), mapOf("Retry-After" to "120"))
        val e = assertFailsWith<RateLimitException> { t.likyly.events.view(EventInput(itemId = "SKU-1", sessionId = "s")) }
        assertEquals(120.0, e.retryAfterSeconds)
        assertTrue(t.sleeps.isEmpty())
    }

    @Test
    fun maxRetriesIsRespected() = runBlocking {
        val t = TestClient(LikylyOptions(maxRetries = 2, cache = null))
        repeat(3) { t.transport.reply(502, "<html>Bad gateway</html>") }
        val e = assertFailsWith<ApiException> { t.likyly.recommendations.get() }
        assertEquals(502, e.statusCode)
        assertEquals("<html>Bad gateway</html>", e.body)
        assertEquals(3, t.transport.requests.size)
    }

    @Test
    fun cacheHitMakesNoSecondRequestAndIgnoresTheAnonymousVisitor() = runBlocking {
        val t = TestClient()
        t.transport.reply(200, placementBody)
        val first = t.likyly.recommend(PlacementRequest("pdp-related", PlacementContext(itemId = "SKU-9", sessionId = "a"), 4))
        val second = t.likyly.recommend(PlacementRequest("pdp-related", PlacementContext(itemId = "SKU-9", sessionId = "b"), 4))
        assertEquals(first, second)
        assertEquals("rec_1", second.recommendationId)
        assertEquals(1, t.transport.requests.size)

        t.clock += 3600 * 1000L // expired
        t.transport.reply(200, placementBody)
        t.likyly.recommend(PlacementRequest("pdp-related", PlacementContext(itemId = "SKU-9"), 4))
        assertEquals(2, t.transport.requests.size)

        t.likyly.clearRecommendationCache()
        t.transport.reply(200, placementBody)
        t.likyly.recommend(PlacementRequest("pdp-related", PlacementContext(itemId = "SKU-9"), 4))
        assertEquals(3, t.transport.requests.size)
    }

    @Test
    fun differentContextIsADifferentCacheEntry() = runBlocking {
        val t = TestClient()
        t.transport.reply(200, placementBody).reply(200, placementBody)
        t.likyly.recommend(PlacementRequest("pdp-related", PlacementContext(itemId = "SKU-1")))
        t.likyly.recommend(PlacementRequest("pdp-related", PlacementContext(itemId = "SKU-2")))
        assertEquals(2, t.transport.requests.size)
    }

    @Test
    fun outagePausesPlacementCalls() = runBlocking {
        val t = TestClient(LikylyOptions(maxRetries = 0))
        t.transport.reply(503, mapOf("detail" to "down"))
        assertFailsWith<ApiException> { t.likyly.recommend(PlacementRequest("home")) }
        val paused = assertFailsWith<PausedException> { t.likyly.recommend(PlacementRequest("home")) }
        assertEquals(30, paused.retryInSeconds)
        assertEquals(1, t.transport.requests.size)

        t.clock += 30_000
        t.transport.reply(200, placementBody)
        t.likyly.recommend(PlacementRequest("home"))
        assertEquals(2, t.transport.requests.size)
    }

    @Test
    fun clientErrorDoesNotPause() = runBlocking {
        val t = TestClient(LikylyOptions(maxRetries = 0))
        t.transport.reply(404, mapOf("detail" to "no such placement")).reply(200, placementBody)
        assertFailsWith<NotFoundException> { t.likyly.recommend(PlacementRequest("typo")) }
        t.likyly.recommend(PlacementRequest("home"))
        assertEquals(2, t.transport.requests.size)
    }

    @Test
    fun pathSegmentsAreEncoded() = runBlocking {
        val t = TestClient()
        t.transport.reply(200, mapOf("recommendation_id" to "r", "strategy" to "content", "items" to emptyList<Any>()))
        t.transport.reply(200, eventBody)
        assertFailsWith<ValidationException> { t.likyly.recommendations.similar("gid://shopify/Product/9") } // "/" cannot go in that path
        t.likyly.recommendations.similar("SKU 9é")
        assertEquals("/getRec/content/SKU%209%C3%A9/10", t.transport.requests[0].rawPath)
        t.likyly.events.track("favorite", EventInput(itemId = "gid://shopify/Product/9", userId = "u"))
        assertEquals(mapOf("user_id" to "u", "item_id" to "gid://shopify/Product/9"), t.transport.requests[1].json)
        assertEquals("gid%3A%2F%2Fshopify%2FProduct%2F9", encodeSegment("gid://shopify/Product/9"))
    }

    @Test
    fun catalogAndUserAgentSuffix() = runBlocking {
        val t = TestClient(LikylyOptions(catalog = "movies", userAgent = "my-app/2.0"))
        t.transport.reply(200, eventBody)
        t.likyly.events.view(EventInput(itemId = "SKU-1", userId = "u"))
        val r = t.transport.requests.single()
        assertEquals("movies", r.query["data_product_type"])
        assertEquals("likyly-android/$LIKYLY_VERSION my-app/2.0", r.headers["User-Agent"])
        assertEquals("application/json", r.headers["Content-Type"])
    }

    @Test
    fun validationBeforeSending() = runBlocking {
        val t = TestClient()
        assertFailsWith<ValidationException> { t.likyly.events.view(EventInput(itemId = "SKU-1")) }
        assertFailsWith<ValidationException> { t.likyly.events.view(EventInput(itemId = " ", userId = "u")) }
        assertFailsWith<ValidationException> { t.likyly.events.track("bad type!", EventInput(itemId = "SKU-1", userId = "u")) }
        assertFailsWith<ValidationException> { t.likyly.events.trackMany(emptyList()) }
        assertFailsWith<ValidationException> { t.likyly.recommendations.session() }
        assertFailsWith<ValidationException> { t.likyly.recommendations.session(listOf("a,b")) }
        assertFailsWith<ValidationException> { t.likyly.recommend(PlacementRequest("home", limit = 0)) }
        assertTrue(t.transport.requests.isEmpty())
    }

    @Test
    fun fileAnonymousIdStorePersists() = runBlocking {
        val dir = Files.createTempDirectory("likyly").toFile()
        val first = LikylySession(FileAnonymousIdStore(dir)).anonymousId
        assertTrue(first.startsWith("anon_"))
        assertEquals(first, LikylySession(FileAnonymousIdStore(dir)).anonymousId) // a new process reads it back
        assertNotEquals(first, LikylySession(InMemoryAnonymousIdStore()).anonymousId)

        val t = TestClient()
        t.transport.reply(200, mapOf("linked_interactions" to 4))
        val result = LikylySession(FileAnonymousIdStore(dir)).identify(t.likyly, "user_42")
        assertEquals(4, result.linkedInteractions)
        assertEquals(mapOf("user_id" to "user_42", "session_id" to first), t.transport.requests.single().json)
        dir.deleteRecursively()
    }

    @Test
    fun jsonRoundTrip() {
        val value = mapOf("s" to "a\"b\\c\n\u0001é", "i" to 42L, "d" to 1.5, "b" to true, "n" to null, "l" to listOf(1L, "x"), "o" to mapOf("k" to "v"))
        assertTrue(jsonEquals(value, Json.parse(Json.encode(value))))
        assertEquals("""{"x":129.9,"y":1}""", Json.encode(mapOf("x" to 129.9, "y" to 1.0)))
        assertFailsWith<IllegalArgumentException> { Json.parse("{bad") }
    }
}
