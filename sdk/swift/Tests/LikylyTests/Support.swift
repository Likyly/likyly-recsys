import Foundation
@testable import Likyly

/// Plays the API: records each request and answers from a queue (the last answer repeats).
final class ScriptedTransport: LikylyTransport, @unchecked Sendable {
    struct Reply {
        var status: Int
        var headers: [String: String] = [:]
        var body: Any? = nil
        var error: URLError? = nil
    }

    private let lock = NSLock()
    private var replies: [Reply]
    private(set) var requests: [URLRequest] = []

    init(_ replies: [Reply]) { self.replies = replies }

    var count: Int { lock.lock(); defer { lock.unlock() }; return requests.count }

    private func next(_ request: URLRequest) -> Reply {
        lock.lock()
        defer { lock.unlock() }
        requests.append(request)
        return replies.count > 1 ? replies.removeFirst() : replies[0]
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let reply = next(request)
        if let error = reply.error { throw error }
        var data = Data()
        if let body = reply.body, !(body is NSNull) {
            data = try JSONSerialization.data(withJSONObject: body, options: [.fragmentsAllowed])
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: reply.headers)!
        return (data, response)
    }
}

final class Sleeps: @unchecked Sendable {
    private let lock = NSLock()
    private(set) var values: [TimeInterval] = []
    func record(_ v: TimeInterval) { lock.lock(); values.append(v); lock.unlock() }
}

final class Clock: @unchecked Sendable {
    private let lock = NSLock()
    private var current = Date(timeIntervalSince1970: 1_800_000_000)
    var now: Date { lock.lock(); defer { lock.unlock() }; return current }
    func advance(_ seconds: TimeInterval) { lock.lock(); current += seconds; lock.unlock() }
}

func makeClient(_ transport: ScriptedTransport, apiKey: String = "pk_test", cache: RecommendCacheOptions? = nil,
                catalog: String? = nil, sleeps: Sleeps = Sleeps(), clock: Clock = Clock()) throws -> Likyly {
    try Likyly(apiKey: apiKey,
               options: LikylyOptions(baseURL: "https://api.example.test", catalog: catalog, cache: cache, transport: transport),
               sleep: { sleeps.record($0) }, random: { 1 }, now: { clock.now })
}

func bodyJSON(_ request: URLRequest) -> Any? {
    guard let data = request.httpBody, !data.isEmpty else { return nil }
    return try? JSONSerialization.jsonObject(with: data)
}
