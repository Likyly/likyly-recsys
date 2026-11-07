import Foundation

/// How placement answers are cached on the device, and how long calls pause after LIKYLY fails.
public struct RecommendCacheOptions: Sendable {
    /// How long an answer is reused, in seconds. Default 3600. 0 disables the cache.
    public var ttlSeconds: TimeInterval
    /// After LIKYLY fails (offline, timeout, 429, 5xx), how long to skip calls, in seconds. Default 30.
    /// 0 always calls.
    public var pauseSeconds: TimeInterval
    public init(ttlSeconds: TimeInterval = 3600, pauseSeconds: TimeInterval = 30) {
        self.ttlSeconds = ttlSeconds
        self.pauseSeconds = pauseSeconds
    }
}

/// In-memory cache + circuit breaker for `placements.recommend`, so a screen does not call LIKYLY on every
/// appearance and does not keep waiting on it when it is down. A reused answer keeps its
/// `recommendationId`, so impressions and clicks stay attributed.
actor RecommendCache {
    static let maxEntries = 500
    private let ttl: TimeInterval
    private let pause: TimeInterval
    private let now: @Sendable () -> Date
    private var entries: [String: (expires: Date, value: PlacementRecommendation)] = [:]
    private var order: [String] = []
    private var pausedUntil = Date.distantPast

    init(_ options: RecommendCacheOptions, now: @escaping @Sendable () -> Date) {
        ttl = max(0, options.ttlSeconds)
        pause = max(0, options.pauseSeconds)
        self.now = now
    }

    func get(_ key: String) -> PlacementRecommendation? {
        guard ttl > 0, let entry = entries[key] else { return nil }
        if entry.expires <= now() {
            entries[key] = nil
            order.removeAll { $0 == key }
            return nil
        }
        return entry.value
    }

    func set(_ key: String, _ value: PlacementRecommendation) {
        guard ttl > 0 else { return }
        if entries[key] == nil { order.append(key) }
        entries[key] = (now().addingTimeInterval(ttl), value)
        if order.count > Self.maxEntries { entries[order.removeFirst()] = nil }
    }

    func clear() {
        entries.removeAll()
        order.removeAll()
    }

    /// Seconds left in the pause; 0 when not paused.
    func pauseRemaining() -> Int {
        let left = pausedUntil.timeIntervalSince(now())
        return left > 0 ? Int(left.rounded(.up)) : 0
    }

    func recordFailure() { if pause > 0 { pausedUntil = now().addingTimeInterval(pause) } }
    func recordSuccess() { pausedUntil = .distantPast }
}

/// `likyly.placements`: the recommended way to ask for recommendations. A placement ("what to recommend,
/// where, with what fallback and filters") is configured once in LIKYLY (console or MCP admin tools, never
/// from the app) and called by its slug from here on.
public final class Placements: Sendable {
    let core: Core
    let cache: RecommendCache?
    init(core: Core, cache: RecommendCache?) {
        self.core = core
        self.cache = cache
    }

    /// Recommendations for one placement. Send whatever context you have (`itemId`, `userId`,
    /// `sessionId`, ...): the placement's own configuration decides the strategy, fallback and filters.
    /// Send `recommendationId` back on the events that follow to attribute them.
    ///
    /// Throws `LikylyError.paused` at once for a short while after LIKYLY failed: show your fallback.
    public func recommend(_ placement: String, context: PlacementContext? = nil, limit: Int? = nil,
                          options: RequestOptions? = nil) async throws -> PlacementRecommendation {
        let slug = try requireId(placement, "placement")
        try requireLimit(limit)
        var body: [String: Any] = [:]
        if let context { body["context"] = Self.contextBody(context) }
        if let limit { body["limit"] = limit }

        let key = Self.cacheKey(slug, body)
        if let cache {
            if let hit = await cache.get(key) { return hit }
            let left = await cache.pauseRemaining()
            if left > 0 { throw LikylyError.paused(resumeIn: left) }
        }
        do {
            // A read in effect: each call mints a fresh recommendation id, so repeating it is harmless.
            let w = try await core.request(Call(method: "POST", path: "/placements/\(encodeSegment(slug))/recommend",
                                                body: body, idempotent: true, options: options)).json()
            let result = PlacementRecommendation(recommendationId: w["recommendation_id"]?.string ?? "",
                                                 placement: w["placement"]?.string ?? slug,
                                                 strategyUsed: w["strategy_used"]?.string ?? "",
                                                 items: items(w))
            await cache?.recordSuccess()
            await cache?.set(key, result)
            return result
        } catch let error as LikylyError {
            if error.isOutage { await cache?.recordFailure() }
            throw error
        }
    }

    static func contextBody(_ c: PlacementContext) -> [String: Any] {
        var body: [String: Any] = [:]
        let fields: [(String, String?)] = [
            ("current_item_id", c.itemId), ("category_id", c.categoryId), ("collection_id", c.collectionId),
            ("user_id", c.userId), ("anonymous_id", c.anonymousId), ("session_id", c.sessionId), ("locale", c.locale),
        ]
        for (key, value) in fields { if let value, !value.isEmpty { body[key] = value } }
        if let ids = c.cartItemIds { body["cart_item_ids"] = ids }
        if let custom = c.customContext { body["custom_context"] = custom.mapValues(\.foundation) }
        return body
    }

    /// Same placement, limit and context = same answer; who the anonymous visitor is does not count.
    static func cacheKey(_ placement: String, _ body: [String: Any]) -> String {
        var context = body["context"] as? [String: Any] ?? [:]
        context["session_id"] = nil
        context["anonymous_id"] = nil
        let json = (try? JSONSerialization.data(withJSONObject: context, options: [.sortedKeys])).map { String(decoding: $0, as: UTF8.self) } ?? ""
        return "\(placement)|\(body["limit"].map { "\($0)" } ?? "")|\(json)"
    }
}
