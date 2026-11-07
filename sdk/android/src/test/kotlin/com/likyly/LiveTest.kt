package com.likyly

import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assumptions.assumeTrue
import java.net.HttpURLConnection
import java.net.URL
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * Against a real API: `sdk/conformance/local-api.sh start`, `source sdk/conformance/.local-api.env`, then
 * `./gradlew test`. Skipped when LIKYLY_TEST_URL / LIKYLY_TEST_SECRET_KEY / LIKYLY_TEST_PUBLIC_KEY are not set.
 *
 * The catalog and the placement are set up with the secret key over plain HTTP, as a backend would; everything
 * the app does goes through this SDK with the public key.
 */
class LiveTest {
    @Test
    fun anAppSessionAgainstTheRealApi() = runBlocking {
        val url = System.getenv("LIKYLY_TEST_URL")
        val secret = System.getenv("LIKYLY_TEST_SECRET_KEY")
        val publicKey = System.getenv("LIKYLY_TEST_PUBLIC_KEY")
        assumeTrue(url != null && secret != null && publicKey != null, "LIKYLY_TEST_* not set")
        val catalog = "android-${System.currentTimeMillis()}"
        val gid = "gid://shopify/Product/42"
        val slug = "pdp-$catalog"

        // Backend side (secret key): a small catalog and a product-page placement.
        for ((id, title) in listOf("SKU-1" to "Nike Pegasus", "SKU-2" to "Adidas Boston", "SKU-3" to "Asics Novablast", gid to "Hoka Clifton")) {
            backend(url!!, secret!!, "PUT", "/items/${encodeSegment(id)}?data_product_type=$catalog",
                mapOf("title" to title, "description" to "road running shoe", "properties" to mapOf("category" to "shoes")))
        }
        backend(url!!, secret!!, "POST", "/placements",
            mapOf("slug" to slug, "name" to "Related (Android live)", "context_type" to "product_page", "product_type" to catalog, "limit" to 3))

        // App side (public key).
        val likyly = Likyly(publicKey!!, LikylyOptions(baseUrl = url, catalog = catalog))
        val session = LikylySession(InMemoryAnonymousIdStore())
        val visitor = session.anonymousId

        likyly.events.view(EventInput(itemId = "SKU-1", sessionId = visitor))
        likyly.events.view(EventInput(itemId = gid, sessionId = visitor))

        val recs = likyly.recommend(PlacementRequest(slug, PlacementContext(itemId = "SKU-1", sessionId = visitor)))
        assertTrue(Regex("^rec_[0-9A-Z]{26}$").matches(recs.recommendationId), recs.recommendationId)
        assertEquals(slug, recs.placement)
        assertTrue(recs.items.isNotEmpty() && recs.items.size <= 3, recs.items.toString())
        assertFalse(recs.items.any { it.itemId == "SKU-1" }, "the current item is excluded")

        // Cached on the device: same answer, same recommendation id, for another visitor too.
        val again = likyly.recommend(PlacementRequest(slug, PlacementContext(itemId = "SKU-1", sessionId = "someone-else")))
        assertEquals(recs.recommendationId, again.recommendationId)

        val shown = recs.items[0]
        val attributed = EventInput(itemId = shown.itemId, sessionId = visitor, recommendationId = recs.recommendationId, placement = recs.placement)
        likyly.events.impression(attributed)
        assertFalse(likyly.events.click(attributed).duplicate)

        // Sign-in: the anonymous history now belongs to the user.
        val userId = "user-$catalog"
        assertTrue(session.identify(likyly, userId).linkedInteractions >= 2)

        val purchase = EventInput(itemId = shown.itemId, userId = userId, eventId = "purchase_${catalog}_1", recommendationId = recs.recommendationId,
            quantity = 1, properties = mapOf("price" to 129.9, "currency" to "EUR"))
        assertFalse(likyly.events.purchase(purchase).duplicate)
        assertTrue(likyly.events.purchase(purchase).duplicate, "a replayed purchase is ignored")

        val batch = likyly.events.trackMany(listOf(TypedEvent("view", EventInput(itemId = "SKU-2", userId = userId)),
            TypedEvent("favorite", EventInput(itemId = "SKU-3", userId = userId))))
        assertEquals(EventBatchResult(received = 2, accepted = 2, duplicates = 0), batch)

        assertEquals("content", likyly.recommendations.get(RecommendationRequest(itemId = gid, limit = 2)).strategy)
        assertTrue(likyly.recommendations.get(RecommendationRequest(userId = userId, limit = 3)).items.isNotEmpty())
        assertEquals("content", likyly.recommendations.similar(itemId = "SKU-2", limit = 2).strategy)
        assertTrue(likyly.recommendations.popular(limit = 2).items.isNotEmpty())

        val notFound = assertFailsWith<NotFoundException> { likyly.recommend(PlacementRequest("no-such-placement")) }
        assertNotNull(notFound.requestId)
        assertFailsWith<AuthenticationException> {
            Likyly("pk_not_a_real_key", LikylyOptions(baseUrl = url, cache = null)).recommend(PlacementRequest("pdp-related"))
        }
        Unit
    }

    private fun backend(url: String, key: String, method: String, path: String, body: Map<String, Any?>) {
        val connection = URL(url + path).openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.doOutput = true
        connection.setRequestProperty("X-API-Key", key)
        connection.setRequestProperty("Content-Type", "application/json")
        connection.outputStream.use { it.write(Json.encode(body).toByteArray()) }
        val status = connection.responseCode
        val text = (if (status < 400) connection.inputStream else connection.errorStream)?.bufferedReader()?.readText()
        assertTrue(status in 200..299, "$method $path: $status $text")
    }
}
