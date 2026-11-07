import Foundation
import XCTest
@testable import Likyly

/// Against a real API: `sdk/conformance/local-api.sh start`, `source sdk/conformance/.local-api.env`, then
/// `swift test`. Skipped when LIKYLY_TEST_URL / LIKYLY_TEST_SECRET_KEY / LIKYLY_TEST_PUBLIC_KEY are not set.
///
/// The catalog and the placement are set up with the secret key over plain HTTP, as a backend would; everything
/// the app does goes through this SDK with the public key.
final class LiveTests: XCTestCase {
    func testAnAppSessionAgainstTheRealAPI() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let url = env["LIKYLY_TEST_URL"], let secret = env["LIKYLY_TEST_SECRET_KEY"], let publicKey = env["LIKYLY_TEST_PUBLIC_KEY"] else {
            throw XCTSkip("LIKYLY_TEST_* not set")
        }
        let catalog = "swift-\(Int(Date().timeIntervalSince1970 * 1000))"
        let gid = "gid://shopify/Product/42"
        let slug = "pdp-\(catalog)"

        // Backend side (secret key): a small catalog and a product-page placement.
        for (id, title) in [("SKU-1", "Nike Pegasus"), ("SKU-2", "Adidas Boston"), ("SKU-3", "Asics Novablast"), (gid, "Hoka Clifton")] {
            try await backend(url, secret, "PUT", "/items/\(encodeSegment(id))?data_product_type=\(catalog)",
                              ["title": title, "description": "road running shoe", "properties": ["category": "shoes"]])
        }
        try await backend(url, secret, "POST", "/placements",
                          ["slug": slug, "name": "Related (Swift live)", "context_type": "product_page", "product_type": catalog, "limit": 3])

        // App side (public key).
        let likyly = try Likyly(apiKey: publicKey, options: LikylyOptions(baseURL: url, catalog: catalog))
        let session = LikylySession(defaults: UserDefaults(suiteName: "likyly-live-\(catalog)")!)
        let visitor = session.anonymousId

        try await likyly.events.view(EventInput(itemId: "SKU-1", sessionId: visitor))
        try await likyly.events.view(EventInput(itemId: gid, sessionId: visitor))

        let recs = try await likyly.recommend(slug, context: PlacementContext(itemId: "SKU-1", sessionId: visitor))
        XCTAssertNotNil(recs.recommendationId.range(of: "^rec_[0-9A-Z]{26}$", options: .regularExpression), recs.recommendationId)
        XCTAssertEqual(recs.placement, slug)
        XCTAssertFalse(recs.items.isEmpty)
        XCTAssertLessThanOrEqual(recs.items.count, 3)
        XCTAssertFalse(recs.items.contains { $0.itemId == "SKU-1" }, "the current item is excluded")

        // Cached on the device: same answer, same recommendation id, for another visitor too.
        let again = try await likyly.recommend(slug, context: PlacementContext(itemId: "SKU-1", sessionId: "someone-else"))
        XCTAssertEqual(again.recommendationId, recs.recommendationId)

        let shown = recs.items[0]
        let attributed = EventInput(itemId: shown.itemId, sessionId: visitor, recommendationId: recs.recommendationId, placement: recs.placement)
        try await likyly.events.impression(attributed)
        let click = try await likyly.events.click(attributed)
        XCTAssertFalse(click.duplicate)

        // Sign-in: the anonymous history now belongs to the user.
        let userId = "user-\(catalog)"
        let linked = try await session.identify(likyly, userId: userId)
        XCTAssertGreaterThanOrEqual(linked.linkedInteractions, 2)

        let purchase = EventInput(itemId: shown.itemId, userId: userId, eventId: "purchase_\(catalog)_1", recommendationId: recs.recommendationId,
                                  quantity: 1, properties: ["price": 129.9, "currency": "EUR"])
        let first = try await likyly.events.purchase(purchase)
        let replay = try await likyly.events.purchase(purchase)
        XCTAssertFalse(first.duplicate)
        XCTAssertTrue(replay.duplicate, "a replayed purchase is ignored")

        let batch = try await likyly.events.trackMany([TypedEvent("view", EventInput(itemId: "SKU-2", userId: userId)),
                                                       TypedEvent("favorite", EventInput(itemId: "SKU-3", userId: userId))])
        XCTAssertEqual(batch, EventBatchResult(received: 2, accepted: 2, duplicates: 0))

        let forItem = try await likyly.recommendations.get(itemId: gid, limit: 2)
        let forUser = try await likyly.recommendations.get(userId: userId, limit: 3)
        let similar = try await likyly.recommendations.similar(itemId: "SKU-2", limit: 2)
        let popular = try await likyly.recommendations.popular(limit: 2)
        XCTAssertEqual(forItem.strategy, "content")
        XCTAssertFalse(forUser.items.isEmpty)
        XCTAssertEqual(similar.strategy, "content")
        XCTAssertFalse(popular.items.isEmpty)

        do {
            _ = try await likyly.recommend("no-such-placement")
            XCTFail("expected notFound")
        } catch LikylyError.notFound(let details) {
            XCTAssertNotNil(details.requestId)
        }
        do {
            _ = try await Likyly(apiKey: "pk_not_a_real_key", options: LikylyOptions(baseURL: url, cache: nil)).recommend("pdp-related")
            XCTFail("expected authentication")
        } catch LikylyError.authentication {}
    }

    @discardableResult
    private func backend(_ url: String, _ key: String, _ method: String, _ path: String, _ body: [String: Any]) async throws -> Data {
        var request = URLRequest(url: URL(string: url + path)!)
        request.httpMethod = method
        request.setValue(key, forHTTPHeaderField: "X-API-Key")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as! HTTPURLResponse).statusCode
        XCTAssertTrue((200..<300).contains(status), "\(method) \(path): \(status) \(String(decoding: data, as: UTF8.self))")
        return data
    }
}
