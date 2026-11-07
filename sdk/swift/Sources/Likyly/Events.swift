import Foundation

/// What your visitors do: `likyly.events`. `track` is the one mechanism; `view`, `click`, ... are shortcuts
/// that call it with the matching event type.
public final class Events: Sendable {
    let core: Core
    init(core: Core) { self.core = core }

    /// Records one event of any type: "view", "click", ... or your own ("favorite", "share", ...).
    ///
    /// Retries: a failed call is only retried automatically when it carries an `eventId` (then a replay is
    /// harmless); without one, an ambiguous failure is thrown rather than risking a duplicate.
    @discardableResult
    public func track(_ type: String, _ event: EventInput, options: RequestOptions? = nil) async throws -> EventResult {
        let t = try requireEventType(type)
        let body = try Self.body(event)
        let w = try await core.request(Call(method: "POST", path: "/events/" + encodeSegment(t), body: body,
                                            idempotent: event.eventId != nil, options: options)).json()
        return EventResult(message: w["message"]?.string ?? "", eventId: w["event_id"]?.string,
                           duplicate: w["duplicate"]?.boolValue ?? false)
    }

    /// Records up to 1000 events in one call, each with its own type. Validation is all-or-nothing.
    @discardableResult
    public func trackMany(_ events: [TypedEvent], options: RequestOptions? = nil) async throws -> EventBatchResult {
        if events.isEmpty { throw validation("events must be a non-empty array") }
        var entries: [[String: Any]] = []
        for e in events {
            var body = try Self.body(e.event)
            body["event_type"] = try requireEventType(e.type)
            entries.append(body)
        }
        let allKeyed = events.allSatisfy { $0.event.eventId != nil }
        let w = try await core.request(Call(method: "POST", path: "/events/batch", body: ["events": entries],
                                            idempotent: allKeyed, options: options)).json()
        return EventBatchResult(received: w["received"]?.int ?? 0, accepted: w["accepted"]?.int ?? 0,
                                duplicates: w["duplicates"]?.int ?? 0)
    }

    /// Ties an anonymous session to the user who just signed in (or signed up): the session's past
    /// interactions now count for `userId`. Call it once at login; calling it again is harmless.
    @discardableResult
    public func identify(userId: String, sessionId: String, options: RequestOptions? = nil) async throws -> IdentifyResult {
        let body: [String: Any] = ["user_id": try requireId(userId, "userId"), "session_id": try requireId(sessionId, "sessionId")]
        let w = try await core.request(Call(method: "POST", path: "/events/identify", body: body, idempotent: true, options: options)).json()
        return IdentifyResult(linkedInteractions: w["linked_interactions"]?.int ?? 0)
    }

    /// The item was shown to the visitor (send the `recommendationId` it came with).
    @discardableResult
    public func impression(_ e: EventInput, options: RequestOptions? = nil) async throws -> EventResult {
        try await track("impression", e, options: options)
    }

    /// The visitor looked at the item (a product screen, an article).
    @discardableResult
    public func view(_ e: EventInput, options: RequestOptions? = nil) async throws -> EventResult {
        try await track("view", e, options: options)
    }

    /// The visitor tapped the item.
    @discardableResult
    public func click(_ e: EventInput, options: RequestOptions? = nil) async throws -> EventResult {
        try await track("click", e, options: options)
    }

    @discardableResult
    public func addToCart(_ e: EventInput, options: RequestOptions? = nil) async throws -> EventResult {
        try await track("add_to_cart", e, options: options)
    }

    @discardableResult
    public func removeFromCart(_ e: EventInput, options: RequestOptions? = nil) async throws -> EventResult {
        try await track("remove_from_cart", e, options: options)
    }

    /// The visitor bought the item. Set `eventId` (e.g. "purchase_<orderId>_<itemId>") so a retry can never
    /// count the purchase twice.
    @discardableResult
    public func purchase(_ e: EventInput, options: RequestOptions? = nil) async throws -> EventResult {
        try await track("purchase", e, options: options)
    }

    static func body(_ e: EventInput) throws -> [String: Any] {
        var body: [String: Any] = ["item_id": try requireId(e.itemId, "itemId")]
        if e.userId == nil && e.sessionId == nil { throw validation("an event needs a userId or a sessionId (or both)") }
        if let v = e.userId { body["user_id"] = try requireId(v, "userId") }
        if let v = e.sessionId { body["session_id"] = try requireId(v, "sessionId") }
        if let v = e.eventId, !v.isEmpty { body["event_id"] = v }
        if let v = e.recommendationId, !v.isEmpty { body["recommendation_id"] = v }
        if let v = e.placement, !v.isEmpty { body["placement"] = v }
        if let v = e.quantity { body["quantity"] = v }
        if let v = e.occurredAt { body["occurred_at"] = formatDate(v) }
        if let v = e.properties { body["properties"] = v.mapValues(\.foundation) }
        return body
    }
}
