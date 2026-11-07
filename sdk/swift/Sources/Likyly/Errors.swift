import Foundation

/// What the API said about a failed call.
public struct APIErrorDetails: Sendable, Equatable {
    /// The HTTP status code.
    public let statusCode: Int
    /// The API's explanation.
    public let message: String
    /// The `request_id` of the failed call (also the `X-Request-ID` header): quote it when reporting a problem.
    public let requestId: String?
    /// The `Retry-After` header in seconds (429/503), when present.
    public let retryAfter: TimeInterval?
    /// The parsed response body, when there was one (an HTML page from a proxy becomes `.string`).
    public let body: JSONValue?
}

/// Every error the SDK throws.
///
///     do { ... } catch LikylyError.paused { showFallback() }
///     catch let error as LikylyError where error.statusCode == 404 { ... }
public enum LikylyError: Error, Sendable, LocalizedError {
    /// Invalid request: caught by the SDK before sending (`api` is nil), or a 422 from the API.
    case validation(message: String, api: APIErrorDetails?)
    /// No HTTP response (offline, DNS, connection reset, ...).
    case network(message: String)
    /// The request timed out (the client's timeout).
    case timeout(seconds: TimeInterval)
    /// LIKYLY failed moments ago: placement calls are skipped for a short pause so your screen shows its
    /// fallback at once. `resumeIn` is in seconds.
    case paused(resumeIn: Int)
    /// 401: missing, invalid or revoked API key.
    case authentication(APIErrorDetails)
    /// 403: e.g. the public key used for a secret-key operation, or a plan limit.
    case permissionDenied(APIErrorDetails)
    /// 404.
    case notFound(APIErrorDetails)
    /// 429: see `retryAfter`.
    case rateLimit(APIErrorDetails)
    /// Any other error status (500, 502, ...).
    case api(APIErrorDetails)

    /// The API's details, for every case that came from an HTTP answer.
    public var details: APIErrorDetails? {
        switch self {
        case .validation(_, let api): return api
        case .authentication(let d), .permissionDenied(let d), .notFound(let d), .rateLimit(let d), .api(let d): return d
        case .network, .timeout, .paused: return nil
        }
    }

    public var statusCode: Int? { details?.statusCode }
    public var requestId: String? { details?.requestId }
    public var retryAfter: TimeInterval? { details?.retryAfter }

    public var errorDescription: String? {
        switch self {
        case .validation(let message, let api):
            return api.map(Self.describe) ?? "likyly: \(message)"
        case .network(let message): return "likyly: could not reach the API: \(message)"
        case .timeout(let seconds): return "likyly: request timed out after \(seconds)s"
        case .paused(let resumeIn): return "likyly: LIKYLY did not answer a moment ago: calls resume in \(resumeIn)s"
        case .authentication(let d), .permissionDenied(let d), .notFound(let d), .rateLimit(let d), .api(let d):
            return Self.describe(d)
        }
    }

    private static func describe(_ d: APIErrorDetails) -> String {
        if let id = d.requestId { return "likyly: \(d.message) (HTTP \(d.statusCode), request_id=\(id))" }
        return "likyly: \(d.message) (HTTP \(d.statusCode))"
    }

    /// LIKYLY down or overloaded, as opposed to a mistake in the call (4xx).
    var isOutage: Bool {
        switch self {
        case .network, .timeout, .rateLimit: return true
        case .api(let d): return d.statusCode >= 500
        default: return false
        }
    }

    static func from(status: Int, body: JSONValue?, requestId: String?, retryAfter: TimeInterval?) -> LikylyError {
        var message = Self.message(of: body)
        if message.isEmpty { message = "API error (HTTP \(status))" }
        let d = APIErrorDetails(statusCode: status, message: message, requestId: requestId, retryAfter: retryAfter, body: body)
        switch status {
        case 401: return .authentication(d)
        case 403: return .permissionDenied(d)
        case 404: return .notFound(d)
        case 422: return .validation(message: message, api: d)
        case 429: return .rateLimit(d)
        default: return .api(d)
        }
    }

    private static func message(of body: JSONValue?) -> String {
        switch body?["detail"] {
        case .string(let s): return s
        case .array(let parts):
            return parts.map { part -> String in
                if case .string(let msg)? = part["msg"] { return msg }
                return String(describing: part.foundation)
            }.joined(separator: "; ")
        case nil, .null?: return ""
        case let other?: return String(describing: other.foundation)
        }
    }
}

func validation(_ message: String) -> LikylyError { .validation(message: message, api: nil) }
