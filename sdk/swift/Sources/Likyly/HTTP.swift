import Foundation

/// What the SDK needs to send a request. `URLSessionTransport` is the default; inject your own for
/// tests, proxies or tracing.
public protocol LikylyTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

/// The default transport, on `URLSession`.
public struct URLSessionTransport: LikylyTransport {
    private let session: URLSession
    public init(session: URLSession = .shared) { self.session = session }

    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        return (data, http)
    }
}

/// Tunes one call (the last argument of every method).
public struct RequestOptions: Sendable {
    /// Per-attempt timeout in seconds, overriding the client's.
    public var timeout: TimeInterval?
    /// Retries for this call, overriding the client's (0 disables them).
    public var maxRetries: Int?
    public init(timeout: TimeInterval? = nil, maxRetries: Int? = nil) {
        self.timeout = timeout
        self.maxRetries = maxRetries
    }
}

struct Call {
    var method: String
    var path: String
    var query: [(String, String)] = []
    var body: [String: Any]?
    /// Whether it is safe to send again when the outcome is unknown (timeout, 5xx, dropped connection):
    /// false for an event without `eventId`, where a blind retry could record it twice.
    var idempotent: Bool
    var options: RequestOptions?
}

struct Response {
    let data: Data
    func json() throws -> JSONValue {
        guard let any = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) else {
            throw LikylyError.api(APIErrorDetails(statusCode: 200, message: "the API answered with a body that is not JSON", requestId: nil, retryAfter: nil, body: nil))
        }
        return JSONValue(foundation: any)
    }
}

/// The only place that talks HTTP: auth header, catalog parameter, timeout, retries with exponential
/// backoff + full jitter, and the error mapping.
final class Core: @unchecked Sendable {
    static let retryBase: TimeInterval = 0.5
    static let retryCap: TimeInterval = 8
    /// A Retry-After longer than this is not waited for: the rateLimit error is thrown instead.
    static let maxRetryAfter: TimeInterval = 60

    let apiKey: String
    let baseURL: String
    let catalog: String?
    let timeout: TimeInterval
    let maxRetries: Int
    let userAgent: String
    let transport: LikylyTransport
    let sleep: @Sendable (TimeInterval) async throws -> Void
    let random: @Sendable () -> Double

    init(apiKey: String, baseURL: String, catalog: String?, timeout: TimeInterval, maxRetries: Int, userAgent: String,
         transport: LikylyTransport, sleep: @escaping @Sendable (TimeInterval) async throws -> Void,
         random: @escaping @Sendable () -> Double) {
        self.apiKey = apiKey
        var base = baseURL
        while base.hasSuffix("/") { base.removeLast() }
        self.baseURL = base
        self.catalog = catalog
        self.timeout = timeout
        self.maxRetries = maxRetries
        self.userAgent = userAgent
        self.transport = transport
        self.sleep = sleep
        self.random = random
    }

    func request(_ call: Call) async throws -> Response {
        let maxRetries = call.options?.maxRetries ?? self.maxRetries
        let timeout = call.options?.timeout ?? self.timeout
        var payload: Data?
        if let body = call.body {
            guard JSONSerialization.isValidJSONObject(body) else { throw validation("request body is not valid JSON") }
            payload = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        }
        guard let url = URL(string: buildURL(call.path, call.query)) else { throw validation("invalid request URL") }

        var attempt = 0
        while true {
            do {
                return try await once(call.method, url, payload, timeout)
            } catch let error as LikylyError {
                guard attempt < maxRetries, let delay = retryDelay(error, attempt: attempt, idempotent: call.idempotent) else { throw error }
                try await sleep(delay)
                attempt += 1
            }
        }
    }

    func retryDelay(_ error: LikylyError, attempt: Int, idempotent: Bool) -> TimeInterval? {
        let backoff = random() * min(Self.retryCap, Self.retryBase * pow(2, Double(attempt)))
        func afterHeader(_ d: APIErrorDetails) -> TimeInterval? {
            guard let after = d.retryAfter else { return backoff }
            return after <= Self.maxRetryAfter ? after : nil
        }
        switch error {
        case .rateLimit(let d):
            // Rejected by the rate limiter before reaching the application: nothing was processed.
            return afterHeader(d)
        case .api(let d) where [502, 503, 504].contains(d.statusCode):
            return idempotent ? afterHeader(d) : nil
        case .network, .timeout:
            return idempotent ? backoff : nil
        default:
            return nil
        }
    }

    func buildURL(_ path: String, _ query: [(String, String)]) -> String {
        var params = query
        if let catalog, !catalog.isEmpty { params.append(("data_product_type", catalog)) }
        var url = baseURL + path
        for (index, (key, value)) in params.enumerated() {
            url += (index == 0 ? "?" : "&") + encodeSegment(key) + "=" + encodeSegment(value)
        }
        return url
    }

    private func once(_ method: String, _ url: URL, _ payload: Data?, _ timeout: TimeInterval) async throws -> Response {
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        if let payload {
            request.httpBody = payload
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let data: Data
        let response: HTTPURLResponse
        do {
            (data, response) = try await transport.send(request)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError {
            if error.code == .cancelled, Task.isCancelled { throw CancellationError() }
            if error.code == .timedOut { throw LikylyError.timeout(seconds: timeout) }
            throw LikylyError.network(message: error.localizedDescription)
        } catch let error as LikylyError {
            throw error
        } catch {
            if Task.isCancelled { throw CancellationError() }
            throw LikylyError.network(message: error.localizedDescription)
        }

        guard response.statusCode >= 400 else { return Response(data: data) }
        var body: JSONValue?
        if !data.isEmpty {
            if let any = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) {
                body = JSONValue(foundation: any)
            } else {
                body = .string(String(decoding: data, as: UTF8.self)) // e.g. an HTML 502 page from a proxy
            }
        }
        var requestId = response.value(forHTTPHeaderField: "X-Request-ID")
        if requestId == nil || requestId == "", case .string(let id)? = body?["request_id"] { requestId = id }
        let retryAfter = Self.parseRetryAfter(response.value(forHTTPHeaderField: "Retry-After"))
        throw LikylyError.from(status: response.statusCode, body: body, requestId: requestId, retryAfter: retryAfter)
    }

    /// Delta-seconds or an HTTP date.
    static func parseRetryAfter(_ value: String?) -> TimeInterval? {
        guard let value = value?.trimmingCharacters(in: .whitespaces), !value.isEmpty else { return nil }
        if let seconds = Double(value), seconds >= 0 { return seconds }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "GMT")
        formatter.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        guard let date = formatter.date(from: value) else { return nil }
        return max(0, date.timeIntervalSinceNow)
    }
}
