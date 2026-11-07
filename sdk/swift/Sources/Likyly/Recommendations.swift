import Foundation

/// `likyly.recommendations`. Use `get`: send what you know and LIKYLY picks the best strategy. The other
/// methods are the advanced recommendations - one strategy at a time. Prefer `placements` in an app.
public final class Recommendations: Sendable {
    static let defaultLimit = 10
    let core: Core
    init(core: Core) { self.core = core }

    /// Recommendations for a user, an anonymous session, an item being viewed, viewed items - or nothing at
    /// all (then you get what is popular). `strategy` in the answer says what was used.
    public func get(userId: String? = nil, sessionId: String? = nil, itemId: String? = nil,
                    viewedItemIds: [String]? = nil, placement: String? = nil, limit: Int? = nil, debug: Bool = false,
                    options: RequestOptions? = nil) async throws -> Recommendation {
        try requireLimit(limit)
        var body: [String: Any] = [:]
        if let userId { body["user_id"] = try requireId(userId, "userId") }
        if let sessionId { body["session_id"] = try requireId(sessionId, "sessionId") }
        if let itemId { body["item_id"] = try requireId(itemId, "itemId") }
        if let viewedItemIds {
            for id in viewedItemIds { try requireId(id, "viewedItemIds[]") }
            body["viewed_item_ids"] = viewedItemIds
        }
        if let placement, !placement.isEmpty { body["placement"] = placement }
        if let limit { body["count"] = limit }
        if debug { body["debug"] = true }
        return try await send(Call(method: "POST", path: "/getRec", body: body, idempotent: true, options: options))
    }

    /// The most popular items - the fallback for a visitor with no history at all.
    public func popular(limit: Int? = nil, placement: String? = nil, sessionId: String? = nil,
                        options: RequestOptions? = nil) async throws -> Recommendation {
        try await advanced("/getRec/popular/\(try limitOf(limit))", placement, sessionId, [], options)
    }

    /// Items similar to one item (content similarity). No user needed.
    public func similar(itemId: String, limit: Int? = nil, placement: String? = nil, sessionId: String? = nil,
                        options: RequestOptions? = nil) async throws -> Recommendation {
        let item = try requirePathSafeId(itemId, "itemId")
        return try await advanced("/getRec/content/\(encodeSegment(item))/\(try limitOf(limit))", placement, sessionId, [], options)
    }

    /// What users with similar histories liked. Needs a trained model.
    public func collaborative(userId: String, limit: Int? = nil, placement: String? = nil, sessionId: String? = nil,
                              options: RequestOptions? = nil) async throws -> Recommendation {
        let user = try requirePathSafeId(userId, "userId")
        return try await advanced("/getRec/collaborative/\(encodeSegment(user))/\(try limitOf(limit))", placement, sessionId, [], options)
    }

    /// Similar items, personalized for a user. `alpha` (0-1) weighs the collaborative signal against content similarity.
    public func hybrid(userId: String, itemId: String, limit: Int? = nil, alpha: Double? = nil, placement: String? = nil,
                       sessionId: String? = nil, options: RequestOptions? = nil) async throws -> Recommendation {
        let user = try requirePathSafeId(userId, "userId")
        let item = try requirePathSafeId(itemId, "itemId")
        let extra: [(String, String)] = alpha.map { [("alpha", Self.format($0))] } ?? []
        return try await advanced("/getRec/hybrid/\(encodeSegment(user))/\(encodeSegment(item))/\(try limitOf(limit))",
                                  placement, sessionId, extra, options)
    }

    /// Recency-weighted recommendations from what was viewed: pass `viewedItemIds` (oldest first) OR
    /// `userId` (LIKYLY's own history of that user's views) - exactly one.
    public func session(viewedItemIds: [String]? = nil, userId: String? = nil, limit: Int? = nil, placement: String? = nil,
                        sessionId: String? = nil, options: RequestOptions? = nil) async throws -> Recommendation {
        if (viewedItemIds != nil) == (userId != nil) {
            throw validation("session needs either viewedItemIds or userId (exactly one)")
        }
        let count = try limitOf(limit)
        if let userId {
            let user = try requirePathSafeId(userId, "userId")
            return try await advanced("/getRec/sessionForUser/\(encodeSegment(user))/\(count)", placement, sessionId, [], options)
        }
        let ids = viewedItemIds ?? []
        if ids.isEmpty { throw validation("viewedItemIds must contain at least one item id") }
        for id in ids {
            try requireId(id, "viewedItemIds[]")
            if id.contains(",") {
                throw validation("an item id containing \",\" cannot be sent in this endpoint's comma-separated list - use recommendations.get")
            }
        }
        return try await advanced("/getRec/session", placement, sessionId,
                                  [("viewed_item_ids", ids.joined(separator: ",")), ("count", count)], options)
    }

    private func advanced(_ path: String, _ placement: String?, _ sessionId: String?, _ extra: [(String, String)],
                          _ options: RequestOptions?) async throws -> Recommendation {
        // response_format=object: always the same {recommendation_id, strategy, items} envelope.
        var query: [(String, String)] = [("response_format", "object")]
        if let placement, !placement.isEmpty { query.append(("placement", placement)) }
        if let sessionId, !sessionId.isEmpty { query.append(("session_id", sessionId)) }
        query += extra
        return try await send(Call(method: "GET", path: path, query: query, idempotent: true, options: options))
    }

    private func send(_ call: Call) async throws -> Recommendation {
        let w = try await core.request(call).json()
        return Recommendation(recommendationId: w["recommendation_id"]?.string ?? "", strategy: w["strategy"]?.string ?? "",
                              placement: w["placement"]?.string, items: items(w))
    }

    private func limitOf(_ limit: Int?) throws -> String {
        try requireLimit(limit)
        return String(limit ?? Self.defaultLimit)
    }

    private static func format(_ value: Double) -> String {
        value.rounded() == value ? String(Int(value)) : String(value)
    }
}
