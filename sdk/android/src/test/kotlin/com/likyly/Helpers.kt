package com.likyly

import java.io.File
import java.io.IOException
import java.net.URI
import java.net.URLDecoder

/** A scripted transport: records every request and plays the queued responses (or throws the queued errors). */
class FakeTransport : LikylyTransport {
    val requests = mutableListOf<HttpRequest>()
    private val queue = ArrayDeque<() -> HttpResponse>()

    fun reply(status: Int, body: Any?, headers: Map<String, String> = emptyMap()): FakeTransport {
        val bytes = when (body) {
            null -> ByteArray(0)
            is String -> body.toByteArray()
            else -> Json.encode(body).toByteArray()
        }
        queue.addLast { HttpResponse(status, headers, bytes) }
        return this
    }

    fun fail(e: IOException): FakeTransport {
        queue.addLast { throw e }
        return this
    }

    override suspend fun send(request: HttpRequest): HttpResponse {
        requests += request
        val next = queue.removeFirstOrNull() ?: error("no scripted response left for ${request.method} ${request.url}")
        return next()
    }
}

val HttpRequest.path: String get() = URI(url).rawPath.let { URLDecoder.decode(it.replace("+", "%2B"), "UTF-8") }
val HttpRequest.rawPath: String get() = URI(url).rawPath

val HttpRequest.query: Map<String, String>
    get() = URI(url).rawQuery?.split('&')?.associate {
        val (k, v) = it.split('=', limit = 2)
        URLDecoder.decode(k, "UTF-8") to URLDecoder.decode(v, "UTF-8")
    } ?: emptyMap()

val HttpRequest.json: Any? get() = body?.let { Json.parse(String(it, Charsets.UTF_8)) }

/** Builds a client with no real waiting: sleeps are recorded, jitter is fixed, the clock is settable. */
class TestClient(
    options: LikylyOptions = LikylyOptions(),
    val transport: FakeTransport = FakeTransport(),
) {
    val sleeps = mutableListOf<Long>()
    var clock = 1_000_000L
    val likyly = Likyly(
        "pk_test",
        options.copy(baseUrl = "https://api.example.test", transport = transport),
        { sleeps += it },
        { 0.5 },
        { clock },
    )
}

fun scenariosFile(): File {
    var dir: File? = File("").absoluteFile
    while (dir != null) {
        val f = File(dir, "conformance/scenarios.json")
        if (f.exists()) return f
        val g = File(dir, "sdk/conformance/scenarios.json")
        if (g.exists()) return g
        dir = dir.parentFile
    }
    error("conformance/scenarios.json not found")
}

/** Numbers compare by value (1 == 1.0), maps ignore key order. */
fun jsonEquals(a: Any?, b: Any?): Boolean = when {
    a is Number && b is Number -> a.toDouble() == b.toDouble()
    a is Map<*, *> && b is Map<*, *> -> a.keys == b.keys && a.keys.all { jsonEquals(a[it], b[it]) }
    a is List<*> && b is List<*> -> a.size == b.size && a.indices.all { jsonEquals(a[it], b[it]) }
    else -> a == b
}

// ---- Results as plain maps with the SDK's (camelCase) field names, to resolve the scenarios' "expect" paths. ----

fun view(v: Any?): Any? = when (v) {
    is PlacementResponse -> mapOf("recommendationId" to v.recommendationId, "placement" to v.placement, "strategyUsed" to v.strategyUsed, "items" to v.items.map(::view))
    is RecommendationResponse -> mapOf("recommendationId" to v.recommendationId, "strategy" to v.strategy, "placement" to v.placement, "items" to v.items.map(::view))
    is RecommendedItem -> mapOf("itemId" to v.itemId, "score" to v.score, "title" to v.title, "description" to v.description, "properties" to v.properties, "explanation" to view(v.explanation))
    is Explanation -> mapOf(
        "reason" to v.reason, "contentSimilarity" to v.contentSimilarity, "semanticSimilarity" to v.semanticSimilarity,
        "popularityScore" to v.popularityScore, "interactionCount" to v.interactionCount, "interactionLabel" to v.interactionLabel,
        "collaborativeScore" to v.collaborativeScore, "sourceItemIds" to v.sourceItemIds,
        "similarUsers" to v.similarUsers?.map { mapOf("userId" to it.userId, "sharedItemIds" to it.sharedItemIds) },
    )
    is EventResult -> mapOf("message" to v.message, "eventId" to v.eventId, "duplicate" to v.duplicate)
    is EventBatchResult -> mapOf("received" to v.received, "accepted" to v.accepted, "duplicates" to v.duplicates)
    is IdentifyResult -> mapOf("linkedInteractions" to v.linkedInteractions)
    else -> v
}

fun resolve(root: Any?, path: String): Any? {
    var cur = root
    for (part in path.split('.')) {
        cur = when {
            part == "length" && cur is List<*> -> cur.size
            cur is Map<*, *> -> cur[part]
            cur is List<*> -> cur[part.toInt()]
            else -> error("cannot resolve '$part' of '$path'")
        }
    }
    return cur
}
