import Foundation
import XCTest
@testable import Likyly

/// Replays sdk/conformance/scenarios.json - the request/response pairs every LIKYLY SDK is checked against -
/// for the operations the public key allows (events, recommendations, placements). The error scenarios are
/// played through placements.recommend, since items/users are server-side only.
final class ConformanceTests: XCTestCase {
    static let scenarios: [[String: Any]] = {
        // A copy next to the package in its own repository, the shared file in the monorepo.
        let package = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let local = package.appendingPathComponent("conformance/scenarios.json")
        let url = FileManager.default.fileExists(atPath: local.path)
            ? local : package.deletingLastPathComponent().appendingPathComponent("conformance/scenarios.json")
        let data = try! Data(contentsOf: url)
        let root = try! JSONSerialization.jsonObject(with: data) as! [String: Any]
        return root["scenarios"] as! [[String: Any]]
    }()

    func testPublicKeyScenarios() async throws {
        var played = 0
        for scenario in Self.scenarios {
            let call = scenario["call"] as! [String: Any]
            let resource = call["resource"] as! String
            guard ["events", "recommendations", "placements"].contains(resource) else { continue }
            let id = scenario["id"] as! String
            let response = scenario["response"] as! [String: Any]
            let transport = ScriptedTransport([.init(status: response["status"] as! Int, headers: response["headers"] as? [String: String] ?? [:], body: response["body"])])
            let likyly = try makeClient(transport)

            let result = try await Self.invoke(likyly, call["method"] as! String, resource, call["args"] as! [Any])
            played += 1

            let request = scenario["request"] as! [String: Any]
            let sent = try XCTUnwrap(transport.requests.first, id)
            XCTAssertEqual(sent.httpMethod, request["method"] as? String, id)
            let components = URLComponents(url: sent.url!, resolvingAgainstBaseURL: false)!
            XCTAssertEqual(components.percentEncodedPath, request["path"] as? String, id)
            let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value ?? "") })
            XCTAssertEqual(query, request["query"] as? [String: String] ?? [:], id)
            if let expected = request["body"] {
                XCTAssertEqual(bodyJSON(sent) as? NSObject, expected as? NSObject, id)
            } else {
                XCTAssertNil(sent.httpBody, id)
            }
            XCTAssertEqual(sent.value(forHTTPHeaderField: "X-API-Key"), "pk_test")
            XCTAssertTrue(sent.value(forHTTPHeaderField: "User-Agent")!.hasPrefix("likyly-swift/"))

            for (path, value) in scenario["expect"] as? [String: Any] ?? [:] {
                XCTAssertEqual(Self.lookup(result, path) as? NSObject, value as? NSObject, "\(id): \(path)")
            }
        }
        XCTAssertEqual(played, 26)
    }

    func testErrorScenarios() async throws {
        var played = 0
        for scenario in Self.scenarios where (scenario["id"] as! String).hasPrefix("errors.") {
            let id = scenario["id"] as! String
            let response = scenario["response"] as! [String: Any]
            let expected = scenario["error"] as! [String: Any]
            let transport = ScriptedTransport([.init(status: response["status"] as! Int, headers: response["headers"] as? [String: String] ?? [:], body: response["body"])])
            let likyly = try makeClient(transport)
            do {
                _ = try await likyly.recommend("homepage", options: RequestOptions(maxRetries: 0))
                XCTFail("\(id): no error")
            } catch let error as LikylyError {
                played += 1
                XCTAssertEqual(Self.className(error), expected["class"] as? String, id)
                XCTAssertEqual(error.statusCode, expected["statusCode"] as? Int, id)
                if let requestId = expected["requestId"] as? String { XCTAssertEqual(error.requestId, requestId, id) }
                if let retryAfter = expected["retryAfter"] as? Double { XCTAssertEqual(error.retryAfter, retryAfter, id) }
                if let message = expected["message"] as? String { XCTAssertEqual(error.details?.message, message, id) }
            }
        }
        XCTAssertEqual(played, 7)
    }

    // MARK: - scenario call -> Swift call

    static func invoke(_ likyly: Likyly, _ method: String, _ resource: String, _ args: [Any]) async throws -> Any {
        let a = args.first as? [String: Any] ?? [:]
        func s(_ key: String) -> String? { a[key] as? String }
        func i(_ key: String) -> Int? { a[key] as? Int }
        switch (resource, method) {
        case ("events", "track"):
            return mirror(try await likyly.events.track(args[0] as! String, event(args[1] as! [String: Any])))
        case ("events", "trackMany"):
            let events = (args[0] as! [[String: Any]]).map { TypedEvent($0["type"] as! String, event($0)) }
            return mirror(try await likyly.events.trackMany(events))
        case ("events", "identify"):
            return mirror(try await likyly.events.identify(userId: s("userId")!, sessionId: s("sessionId")!))
        case ("events", let shortcut):
            let e = event(a)
            let r: EventResult
            switch shortcut {
            case "view": r = try await likyly.events.view(e)
            case "impression": r = try await likyly.events.impression(e)
            case "click": r = try await likyly.events.click(e)
            case "addToCart": r = try await likyly.events.addToCart(e)
            case "removeFromCart": r = try await likyly.events.removeFromCart(e)
            case "purchase": r = try await likyly.events.purchase(e)
            default: fatalError("unknown events.\(shortcut)")
            }
            return mirror(r)
        case ("placements", "recommend"):
            let c = a["context"] as? [String: Any]
            let context = c.map { PlacementContext(itemId: $0["itemId"] as? String, userId: $0["userId"] as? String, sessionId: $0["sessionId"] as? String) }
            return mirror(try await likyly.placements.recommend(s("placement")!, context: context, limit: i("limit")))
        case ("recommendations", "get"):
            return mirror(try await likyly.recommendations.get(userId: s("userId"), sessionId: s("sessionId"), itemId: s("itemId"),
                                                               viewedItemIds: a["viewedItemIds"] as? [String], placement: s("placement"), limit: i("limit")))
        case ("recommendations", "popular"):
            return mirror(try await likyly.recommendations.popular(limit: i("limit"), placement: s("placement"), sessionId: s("sessionId")))
        case ("recommendations", "similar"):
            return mirror(try await likyly.recommendations.similar(itemId: s("itemId")!, limit: i("limit"), placement: s("placement"), sessionId: s("sessionId")))
        case ("recommendations", "collaborative"):
            return mirror(try await likyly.recommendations.collaborative(userId: s("userId")!, limit: i("limit"), placement: s("placement"), sessionId: s("sessionId")))
        case ("recommendations", "hybrid"):
            return mirror(try await likyly.recommendations.hybrid(userId: s("userId")!, itemId: s("itemId")!, limit: i("limit"), alpha: a["alpha"] as? Double))
        case ("recommendations", "session"):
            return mirror(try await likyly.recommendations.session(viewedItemIds: a["viewedItemIds"] as? [String], userId: s("userId"), limit: i("limit")))
        default:
            fatalError("unknown \(resource).\(method)")
        }
    }

    static func event(_ a: [String: Any]) -> EventInput {
        var occurredAt: Date?
        if let raw = a["occurredAt"] as? String { occurredAt = ISO8601DateFormatter().date(from: raw) }
        return EventInput(itemId: a["itemId"] as! String, userId: a["userId"] as? String, sessionId: a["sessionId"] as? String,
                          eventId: a["eventId"] as? String, recommendationId: a["recommendationId"] as? String,
                          placement: a["placement"] as? String, quantity: a["quantity"] as? Int, occurredAt: occurredAt,
                          properties: (a["properties"] as? [String: Any]).map { $0.mapValues(JSONValue.init(foundation:)) })
    }

    /// The result as plain dictionaries, so the scenario's dotted `expect` paths can be looked up.
    static func mirror(_ value: Any) -> Any {
        if let v = value as? JSONValue { return v.foundation }
        if let d = value as? [String: JSONValue] { return d.mapValues(\.foundation) }
        let m = Mirror(reflecting: value)
        if m.displayStyle == .optional { return m.children.first.map { mirror($0.value) } ?? NSNull() }
        if m.displayStyle == .collection { return m.children.map { mirror($0.value) } }
        if m.displayStyle == .struct {
            var out: [String: Any] = [:]
            for child in m.children { if let label = child.label { out[label] = mirror(child.value) } }
            return out
        }
        return value
    }

    static func lookup(_ root: Any, _ path: String) -> Any? {
        var current: Any? = root
        for part in path.split(separator: ".") {
            if part == "length", let array = current as? [Any] { current = array.count; continue }
            if let index = Int(part), let array = current as? [Any] { current = index < array.count ? array[index] : nil; continue }
            current = (current as? [String: Any])?[String(part)]
        }
        return current
    }

    static func className(_ error: LikylyError) -> String {
        switch error {
        case .validation: return "ValidationError"
        case .authentication: return "AuthenticationError"
        case .permissionDenied: return "PermissionDeniedError"
        case .notFound: return "NotFoundError"
        case .rateLimit: return "RateLimitError"
        case .api: return "ApiError"
        case .network: return "NetworkError"
        case .timeout: return "TimeoutError"
        case .paused: return "PausedError"
        }
    }
}
