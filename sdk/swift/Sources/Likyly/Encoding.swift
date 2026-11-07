import Foundation

/// Percent-encodes a path segment: everything outside RFC 3986 "unreserved" (A-Z a-z 0-9 - . _ ~) becomes
/// %XX (UTF-8), "/" included - so an id like "gid://shopify/Product/1" stays one segment.
func encodeSegment(_ s: String) -> String {
    let hex = Array("0123456789ABCDEF")
    var out = ""
    for byte in s.utf8 {
        switch byte {
        case UInt8(ascii: "A")...UInt8(ascii: "Z"), UInt8(ascii: "a")...UInt8(ascii: "z"), UInt8(ascii: "0")...UInt8(ascii: "9"),
             UInt8(ascii: "-"), UInt8(ascii: "."), UInt8(ascii: "_"), UInt8(ascii: "~"):
            out.append(Character(UnicodeScalar(byte)))
        default:
            out.append("%")
            out.append(hex[Int(byte >> 4)])
            out.append(hex[Int(byte & 15)])
        }
    }
    return out
}

/// Ids are opaque, non-empty strings.
@discardableResult
func requireId(_ value: String, _ name: String) throws -> String {
    if value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        throw validation("\(name) must be a non-empty string (your own identifier, e.g. \"SKU-123\")")
    }
    return value
}

/// The advanced recommendation endpoints carry ids in the URL path, where "/" cannot be used.
func requirePathSafeId(_ value: String, _ name: String) throws -> String {
    let id = try requireId(value, name)
    if id.contains("/") {
        throw validation("\(name) \"\(id)\" contains \"/\", which this endpoint cannot carry in its URL path - use recommendations.get, which takes ids in the request body")
    }
    return id
}

func requireEventType(_ type: String) throws -> String {
    let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if type.isEmpty || type.count > 64 || type.unicodeScalars.contains(where: { !allowed.contains($0) }) {
        throw validation("event type must be 1-64 characters of letters, digits, \"_\" or \"-\" (e.g. \"view\", \"add_to_cart\", \"favorite\")")
    }
    return type
}

func requireLimit(_ limit: Int?) throws {
    if let limit, limit <= 0 { throw validation("limit must be a positive integer") }
}

func formatDate(_ date: Date) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.timeZone = TimeZone(identifier: "UTC")
    let hasFraction = date.timeIntervalSince1970.truncatingRemainder(dividingBy: 1) != 0
    formatter.formatOptions = hasFraction ? [.withInternetDateTime, .withFractionalSeconds] : [.withInternetDateTime]
    return formatter.string(from: date)
}
