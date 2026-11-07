import Foundation
import XCTest
@testable import Likyly

final class BehaviorTests: XCTestCase {
    let ok: [String: Any] = ["recommendation_id": "rec_1", "placement": "home", "strategy_used": "popular", "items": [["item_id": "A", "properties": [:]]]]
    let recorded: [String: Any] = ["message": "ok", "event_id": NSNull(), "duplicate": false]

    func testSecretAndDeveloperKeysAreRefused() {
        for key in ["sk_live_123", "lk_dev_123", "", "  "] {
            XCTAssertThrowsError(try Likyly(apiKey: key), key) { error in
                guard case LikylyError.validation = error else { return XCTFail("\(error)") }
            }
        }
        XCTAssertNoThrow(try Likyly(apiKey: "pk_live_123"))
        XCTAssertNoThrow(try Likyly(apiKey: "legacy-unprefixed-key"))
    }

    func testEventWithoutEventIdIsNotRetried() async throws {
        let transport = ScriptedTransport([.init(status: 503, body: ["detail": "down"])])
        let likyly = try makeClient(transport)
        do {
            try await likyly.events.view(EventInput(itemId: "A", userId: "u"))
            XCTFail("expected an error")
        } catch let error as LikylyError {
            XCTAssertEqual(error.statusCode, 503)
        }
        XCTAssertEqual(transport.count, 1)
    }

    func testEventWithEventIdIsRetried() async throws {
        let transport = ScriptedTransport([.init(status: 503, body: ["detail": "down"]), .init(status: 200, body: recorded)])
        let sleeps = Sleeps()
        let likyly = try makeClient(transport, sleeps: sleeps)
        try await likyly.events.purchase(EventInput(itemId: "A", userId: "u", eventId: "purchase_1_A"))
        XCTAssertEqual(transport.count, 2)
        XCTAssertEqual(sleeps.values, [0.5])
    }

    func testRateLimitIsRetriedAfterRetryAfterEvenForEvents() async throws {
        let transport = ScriptedTransport([.init(status: 429, headers: ["Retry-After": "3"], body: ["detail": "slow down"]), .init(status: 200, body: recorded)])
        let sleeps = Sleeps()
        let likyly = try makeClient(transport, sleeps: sleeps)
        try await likyly.events.click(EventInput(itemId: "A", sessionId: "s"))
        XCTAssertEqual(sleeps.values, [3])
        XCTAssertEqual(transport.count, 2)
    }

    func testDroppedConnectionIsANetworkErrorAndRetriedForReads() async throws {
        let transport = ScriptedTransport([.init(status: 0, error: URLError(.networkConnectionLost)), .init(status: 200, body: ok)])
        let likyly = try makeClient(transport)
        let result = try await likyly.recommend("home")
        XCTAssertEqual(result.items.map(\.itemId), ["A"])
        XCTAssertEqual(transport.count, 2)
    }

    func testTimeoutError() async throws {
        let transport = ScriptedTransport([.init(status: 0, error: URLError(.timedOut))])
        let likyly = try makeClient(transport)
        do {
            _ = try await likyly.recommend("home", options: RequestOptions(timeout: 2, maxRetries: 0))
            XCTFail("expected an error")
        } catch LikylyError.timeout(let seconds) {
            XCTAssertEqual(seconds, 2)
        }
    }

    func testPlacementAnswersAreCachedPerContextButNotPerVisitor() async throws {
        let transport = ScriptedTransport([.init(status: 200, body: ok)])
        let likyly = try makeClient(transport, cache: RecommendCacheOptions())
        _ = try await likyly.recommend("home", context: PlacementContext(itemId: "A", sessionId: "s1"))
        let second = try await likyly.recommend("home", context: PlacementContext(itemId: "A", sessionId: "s2"))
        XCTAssertEqual(second.recommendationId, "rec_1")
        XCTAssertEqual(transport.count, 1)
        _ = try await likyly.recommend("home", context: PlacementContext(itemId: "B"))
        XCTAssertEqual(transport.count, 2)
        await likyly.clearRecommendationCache()
        _ = try await likyly.recommend("home", context: PlacementContext(itemId: "A"))
        XCTAssertEqual(transport.count, 3)
    }

    func testCacheExpires() async throws {
        let transport = ScriptedTransport([.init(status: 200, body: ok)])
        let clock = Clock()
        let likyly = try makeClient(transport, cache: RecommendCacheOptions(ttlSeconds: 60), clock: clock)
        _ = try await likyly.recommend("home")
        clock.advance(61)
        _ = try await likyly.recommend("home")
        XCTAssertEqual(transport.count, 2)
    }

    func testCallsPauseAfterAnOutage() async throws {
        let transport = ScriptedTransport([.init(status: 503, body: ["detail": "down"]), .init(status: 200, body: ok)])
        let clock = Clock()
        let likyly = try makeClient(transport, cache: RecommendCacheOptions(), clock: clock)
        do { _ = try await likyly.recommend("home", options: RequestOptions(maxRetries: 0)); XCTFail("expected an error") } catch LikylyError.api {}
        do {
            _ = try await likyly.recommend("home")
            XCTFail("expected a pause")
        } catch LikylyError.paused(let resumeIn) {
            XCTAssertEqual(resumeIn, 30)
        }
        XCTAssertEqual(transport.count, 1)
        clock.advance(31)
        _ = try await likyly.recommend("home")
        XCTAssertEqual(transport.count, 2)
    }

    func testClientErrorsDoNotPause() async throws {
        let transport = ScriptedTransport([.init(status: 404, body: ["detail": "no placement"]), .init(status: 200, body: ok)])
        let likyly = try makeClient(transport, cache: RecommendCacheOptions())
        do { _ = try await likyly.recommend("nope"); XCTFail("expected an error") } catch LikylyError.notFound {}
        _ = try await likyly.recommend("home")
        XCTAssertEqual(transport.count, 2)
    }

    func testIdsAreOneEncodedPathSegment() async throws {
        let transport = ScriptedTransport([.init(status: 200, body: ok)])
        let likyly = try makeClient(transport, catalog: "films & séries")
        _ = try await likyly.recommend("gid://shopify/Product/9")
        let url = transport.requests[0].url!.absoluteString
        XCTAssertEqual(url, "https://api.example.test/placements/gid%3A%2F%2Fshopify%2FProduct%2F9/recommend?data_product_type=films%20%26%20s%C3%A9ries")
    }

    func testValidationHappensBeforeSending() async throws {
        let transport = ScriptedTransport([.init(status: 200, body: recorded)])
        let likyly = try makeClient(transport)
        await XCTAssertThrowsAsync { try await likyly.events.view(EventInput(itemId: "A")) }
        await XCTAssertThrowsAsync { try await likyly.events.track("bad type!", EventInput(itemId: "A", userId: "u")) }
        await XCTAssertThrowsAsync { try await likyly.recommendations.similar(itemId: "a/b") }
        await XCTAssertThrowsAsync { try await likyly.recommendations.session() }
        await XCTAssertThrowsAsync { try await likyly.recommend("home", limit: 0) }
        XCTAssertEqual(transport.count, 0)
    }

    func testSessionPersistsItsAnonymousIdAndIdentifies() async throws {
        let defaults = UserDefaults(suiteName: "likyly-tests-\(UUID().uuidString)")!
        let session = LikylySession(defaults: defaults)
        let id = session.anonymousId
        XCTAssertTrue(id.hasPrefix("anon_"))
        XCTAssertEqual(LikylySession(defaults: defaults).anonymousId, id)

        let transport = ScriptedTransport([.init(status: 200, body: ["linked_interactions": 4])])
        let result = try await session.identify(try makeClient(transport), userId: "user_1")
        XCTAssertEqual(result.linkedInteractions, 4)
        XCTAssertEqual(bodyJSON(transport.requests[0]) as? [String: String], ["user_id": "user_1", "session_id": id])

        session.reset()
        XCTAssertNotEqual(session.anonymousId, id)
    }
}

func XCTAssertThrowsAsync(_ body: () async throws -> Any, file: StaticString = #filePath, line: UInt = #line) async {
    do {
        _ = try await body()
        XCTFail("expected an error", file: file, line: line)
    } catch {}
}
