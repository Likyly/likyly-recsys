import Foundation

// MARK: - Events

/// One thing a visitor did with an item. Send `userId` (signed in), `sessionId` (anonymous) or both.
public struct EventInput: Sendable {
    public var itemId: String
    public var userId: String?
    /// An anonymous visitor / browsing session - no login needed (see `LikylySession`).
    public var sessionId: String?
    /// Your own unique id for the event: set it and a retry can never record it twice.
    public var eventId: String?
    /// The `recommendationId` of the recommendation that surfaced this item - enables attribution.
    public var recommendationId: String?
    /// Where this happened: "homepage", "product_page", "cart", ... or a placement slug.
    public var placement: String?
    public var quantity: Int?
    /// When it happened; nil means "now" (the server's time).
    public var occurredAt: Date?
    /// Your own properties, sent as is.
    public var properties: Properties?

    public init(itemId: String, userId: String? = nil, sessionId: String? = nil, eventId: String? = nil,
                recommendationId: String? = nil, placement: String? = nil, quantity: Int? = nil,
                occurredAt: Date? = nil, properties: Properties? = nil) {
        self.itemId = itemId
        self.userId = userId
        self.sessionId = sessionId
        self.eventId = eventId
        self.recommendationId = recommendationId
        self.placement = placement
        self.quantity = quantity
        self.occurredAt = occurredAt
        self.properties = properties
    }
}

/// An event with its own type, for `events.trackMany`.
public struct TypedEvent: Sendable {
    public var type: String
    public var event: EventInput
    public init(_ type: String, _ event: EventInput) {
        self.type = type
        self.event = event
    }
}

public struct EventResult: Sendable, Equatable {
    public let message: String
    /// The event id, when you sent one.
    public let eventId: String?
    /// True when an event with the same `eventId` was already recorded (and this one was ignored).
    public let duplicate: Bool
}

public struct EventBatchResult: Sendable, Equatable {
    public let received: Int
    public let accepted: Int
    /// Events skipped because their `eventId` was already recorded.
    public let duplicates: Int
}

public struct IdentifyResult: Sendable, Equatable {
    /// How many past interactions of the session now count for the user.
    public let linkedInteractions: Int
}

// MARK: - Placements

/// What you know about the screen: send what you have, the placement's configuration decides the rest.
public struct PlacementContext: Sendable, Equatable {
    /// The item on screen (a product page).
    public var itemId: String?
    public var categoryId: String?
    public var collectionId: String?
    public var userId: String?
    public var anonymousId: String?
    public var sessionId: String?
    public var cartItemIds: [String]?
    public var locale: String?
    public var customContext: Properties?

    public init(itemId: String? = nil, categoryId: String? = nil, collectionId: String? = nil, userId: String? = nil,
                anonymousId: String? = nil, sessionId: String? = nil, cartItemIds: [String]? = nil,
                locale: String? = nil, customContext: Properties? = nil) {
        self.itemId = itemId
        self.categoryId = categoryId
        self.collectionId = collectionId
        self.userId = userId
        self.anonymousId = anonymousId
        self.sessionId = sessionId
        self.cartItemIds = cartItemIds
        self.locale = locale
        self.customContext = customContext
    }
}

/// The answer of a placement. Send `recommendationId` back on the impression, click and purchase events.
public struct PlacementRecommendation: Sendable, Equatable {
    public let recommendationId: String
    public let placement: String
    /// The strategy LIKYLY actually used ("hybrid", "popular", ...).
    public let strategyUsed: String
    public let items: [RecommendedItem]
}

// MARK: - Recommendations

public struct RecommendedItem: Sendable, Equatable, Identifiable {
    public let itemId: String
    public let score: Double?
    public let title: String?
    public let description: String?
    public let properties: Properties
    public let explanation: Explanation?
    public var id: String { itemId }
}

public struct Explanation: Sendable, Equatable {
    public let reason: String
    public let contentSimilarity: Double?
    public let semanticSimilarity: Double?
    public let popularityScore: Double?
    public let interactionCount: Int?
    public let interactionLabel: String?
    public let collaborativeScore: Double?
    public let sourceItemIds: [String]?
    /// Only present with `debug` and a secret key.
    public let similarUsers: [SimilarUser]?
}

public struct SimilarUser: Sendable, Equatable {
    public let userId: String
    public let sharedItemIds: [String]
}

/// The answer of `recommendations.*`.
public struct Recommendation: Sendable, Equatable {
    public let recommendationId: String
    /// The strategy LIKYLY used.
    public let strategy: String
    public let placement: String?
    public let items: [RecommendedItem]
}

// MARK: - Wire decoding

extension JSONValue {
    var string: String? { stringValue }
    var int: Int? { doubleValue.map { Int($0) } }
    var stringArray: [String]? {
        guard case .array(let a) = self else { return nil }
        return a.compactMap(\.stringValue)
    }
    var objectValue: [String: JSONValue]? { if case .object(let o) = self { return o } else { return nil } }
    var arrayValue: [JSONValue]? { if case .array(let a) = self { return a } else { return nil } }
}

extension RecommendedItem {
    init(wire w: JSONValue) {
        itemId = w["item_id"]?.string ?? ""
        score = w["score"]?.doubleValue
        title = w["title"]?.string
        description = w["description"]?.string
        properties = w["properties"]?.objectValue ?? [:]
        if let e = w["explanation"], e.objectValue != nil {
            explanation = Explanation(
                reason: e["reason"]?.string ?? "",
                contentSimilarity: e["content_similarity"]?.doubleValue,
                semanticSimilarity: e["semantic_similarity"]?.doubleValue,
                popularityScore: e["popularity_score"]?.doubleValue,
                interactionCount: e["interaction_count"]?.int,
                interactionLabel: e["interaction_label"]?.string,
                collaborativeScore: e["collaborative_score"]?.doubleValue,
                sourceItemIds: e["source_item_ids"]?.stringArray,
                similarUsers: e["similar_users"]?.arrayValue?.map {
                    SimilarUser(userId: $0["user_id"]?.string ?? "", sharedItemIds: $0["shared_item_ids"]?.stringArray ?? [])
                })
        } else {
            explanation = nil
        }
    }
}

func items(_ w: JSONValue) -> [RecommendedItem] {
    (w["items"]?.arrayValue ?? []).map(RecommendedItem.init(wire:))
}
