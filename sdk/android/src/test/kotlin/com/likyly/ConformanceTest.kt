package com.likyly

import kotlinx.coroutines.runBlocking
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * Replays the shared scenarios (sdk/conformance/scenarios.json) that the public key can run: events,
 * recommendations, placements - and every errors.* response, played back through placements.recommend.
 */
@Suppress("UNCHECKED_CAST")
class ConformanceTest {
    private val scenarios: List<Map<String, Any?>> =
        (Json.parse(scenariosFile().readText()) as Map<String, Any?>)["scenarios"] as List<Map<String, Any?>>

    @Test
    fun publicKeyScenarios() = runBlocking {
        val publicOnes = scenarios.filter { (it["call"] as Map<String, Any?>)["resource"] in setOf("events", "recommendations", "placements") }
        assertTrue(publicOnes.size >= 26, "expected the public-key scenarios, got ${publicOnes.size}")
        for (s in publicOnes) {
            val id = s["id"] as String
            val response = s["response"] as Map<String, Any?>
            val t = TestClient(LikylyOptions(cache = null))
            t.transport.reply((response["status"] as Number).toInt(), response["body"], response["headers"] as Map<String, String>)
            val result = try {
                dispatch(t.likyly, s["call"] as Map<String, Any?>)
            } catch (e: Exception) {
                fail("$id threw $e")
            }

            val expected = s["request"] as Map<String, Any?>
            val sent = t.transport.requests.single()
            assertEquals(expected["method"], sent.method, id)
            assertEquals(expected["path"], sent.path, id)
            val expectedQuery = (expected["query"] as Map<String, Any?>).mapValues { it.value.toString() }
            assertEquals(expectedQuery, sent.query, id)
            if ("body" in expected) {
                assertTrue(jsonEquals(expected["body"], sent.json), "$id body: expected ${Json.encode(expected["body"])}, sent ${sent.json?.let(Json::encode)}")
            }
            assertEquals("pk_test", sent.headers["X-API-Key"], id)
            assertTrue(sent.headers["User-Agent"]!!.startsWith("likyly-android/"), id)

            val expect = s["expect"] as Map<String, Any?>? ?: emptyMap()
            val actual = view(result)
            for ((path, value) in expect) {
                assertTrue(jsonEquals(value, resolve(actual, path)), "$id: $path expected $value, got ${resolve(actual, path)}")
            }
        }
    }

    @Test
    fun errorScenarios() = runBlocking {
        val errors = scenarios.filter { (it["id"] as String).startsWith("errors.") }
        assertTrue(errors.isNotEmpty())
        for (s in errors) {
            val id = s["id"] as String
            val response = s["response"] as Map<String, Any?>
            val err = s["error"] as Map<String, Any?>
            val t = TestClient(LikylyOptions(cache = null, maxRetries = 0))
            t.transport.reply((response["status"] as Number).toInt(), response["body"], response["headers"] as Map<String, String>)
            val thrown = try {
                t.likyly.placements.recommend(PlacementRequest("homepage"))
                fail("$id did not throw")
            } catch (e: LikylyException) {
                e
            }
            val expectedClass = when (err["class"]) {
                "AuthenticationError" -> AuthenticationException::class
                "PermissionDeniedError" -> PermissionDeniedException::class
                "NotFoundError" -> NotFoundException::class
                "ValidationError" -> ValidationException::class
                "RateLimitError" -> RateLimitException::class
                "ApiError" -> ApiException::class
                else -> fail("unknown class ${err["class"]}")
            }
            assertEquals(expectedClass, thrown::class, id)
            assertEquals((err["statusCode"] as Number).toInt(), thrown.httpStatus, id)
            err["requestId"]?.let {
                val rid = (thrown as? ApiException)?.requestId ?: (thrown as? ValidationException)?.requestId
                assertEquals<Any?>(it, rid, id)
            }
            err["message"]?.let { assertEquals<Any?>(it, thrown.message, id) }
            err["retryAfter"]?.let { assertEquals((it as Number).toDouble(), (thrown as ApiException).retryAfterSeconds, id) }
        }
    }

    private suspend fun dispatch(l: Likyly, call: Map<String, Any?>): Any? {
        val args = call["args"] as List<Any?>
        val a0 = args.getOrNull(0) as? Map<String, Any?> ?: emptyMap()
        return when (call["resource"]) {
            "events" -> when (val m = call["method"]) {
                "track" -> l.events.track(args[0] as String, eventInput(args[1] as Map<String, Any?>))
                "trackMany" -> l.events.trackMany((args[0] as List<Map<String, Any?>>).map { TypedEvent(it["type"] as String, eventInput(it)) })
                "identify" -> l.events.identify(a0["userId"] as String, a0["sessionId"] as String)
                "view" -> l.events.view(eventInput(a0))
                "impression" -> l.events.impression(eventInput(a0))
                "click" -> l.events.click(eventInput(a0))
                "addToCart" -> l.events.addToCart(eventInput(a0))
                "removeFromCart" -> l.events.removeFromCart(eventInput(a0))
                "purchase" -> l.events.purchase(eventInput(a0))
                else -> fail("unknown events.$m")
            }
            "placements" -> {
                val ctx = (a0["context"] as Map<String, Any?>?)?.let {
                    PlacementContext(
                        itemId = it["itemId"] as String?, categoryId = it["categoryId"] as String?, collectionId = it["collectionId"] as String?,
                        userId = it["userId"] as String?, anonymousId = it["anonymousId"] as String?, sessionId = it["sessionId"] as String?,
                        cartItemIds = it["cartItemIds"] as List<String>?, locale = it["locale"] as String?,
                        customContext = it["customContext"] as Map<String, Any?>?,
                    )
                }
                l.placements.recommend(PlacementRequest(a0["placement"] as String, ctx, (a0["limit"] as Number?)?.toInt()))
            }
            "recommendations" -> {
                val limit = (a0["limit"] as Number?)?.toInt()
                val placement = a0["placement"] as String?
                val sessionId = a0["sessionId"] as String?
                when (val m = call["method"]) {
                    "get" -> if (args.isEmpty()) l.recommendations.get() else l.recommendations.get(
                        RecommendationRequest(
                            userId = a0["userId"] as String?, sessionId = sessionId, itemId = a0["itemId"] as String?,
                            viewedItemIds = a0["viewedItemIds"] as List<String>?, placement = placement, limit = limit,
                        ),
                    )
                    "popular" -> l.recommendations.popular(limit, placement, sessionId)
                    "similar" -> l.recommendations.similar(a0["itemId"] as String, limit, placement, sessionId)
                    "collaborative" -> l.recommendations.collaborative(a0["userId"] as String, limit, placement, sessionId)
                    "hybrid" -> l.recommendations.hybrid(a0["userId"] as String, a0["itemId"] as String, limit, (a0["alpha"] as Number?)?.toDouble(), placement, sessionId)
                    "session" -> l.recommendations.session(a0["viewedItemIds"] as List<String>?, a0["userId"] as String?, limit, placement, sessionId)
                    else -> fail("unknown recommendations.$m")
                }
            }
            else -> fail("unexpected resource ${call["resource"]}")
        }
    }

    private fun eventInput(m: Map<String, Any?>) = EventInput(
        itemId = m["itemId"] as String,
        userId = m["userId"] as String?,
        sessionId = m["sessionId"] as String?,
        eventId = m["eventId"] as String?,
        recommendationId = m["recommendationId"] as String?,
        placement = m["placement"] as String?,
        quantity = (m["quantity"] as Number?)?.toInt(),
        occurredAt = (m["occurredAt"] as String?)?.let {
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }.parse(it)
        },
        properties = m["properties"] as Map<String, Any?>?,
    )
}
