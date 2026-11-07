import Foundation

/// The SDK version, sent in the User-Agent header.
public let likylyVersion = "1.0.0"

/// Client options.
public struct LikylyOptions: Sendable {
    /// Defaults to https://api.likyly.com.
    public var baseURL: String
    /// Which of your catalogs to use, if your account has several. Leave nil if you have one.
    public var catalog: String?
    /// Per-attempt timeout in seconds. Default 10.
    public var timeout: TimeInterval
    /// Automatic retries on transient failures (429, 502, 503, 504, dropped connections). Default 2.
    public var maxRetries: Int
    /// Appended to the SDK's User-Agent, e.g. "my-shop-ios/4.2".
    public var userAgent: String?
    /// Placement answers are cached for one hour, and calls pause 30 s after LIKYLY fails. nil turns it off.
    public var cache: RecommendCacheOptions?
    /// The HTTP transport. Default: `URLSession.shared`.
    public var transport: LikylyTransport

    public init(baseURL: String = "https://api.likyly.com", catalog: String? = nil, timeout: TimeInterval = 10,
                maxRetries: Int = 2, userAgent: String? = nil, cache: RecommendCacheOptions? = RecommendCacheOptions(),
                transport: LikylyTransport = URLSessionTransport()) {
        self.baseURL = baseURL
        self.catalog = catalog
        self.timeout = timeout
        self.maxRetries = maxRetries
        self.userAgent = userAgent
        self.cache = cache
        self.transport = transport
    }
}

/// The LIKYLY client for iOS, iPadOS, macOS, tvOS and watchOS apps. It runs on the visitor's device, so it
/// takes the **public** key and covers what that key allows:
///
///     likyly.placements       what to show, where (recommended)
///     likyly.events           what visitors do
///     likyly.recommendations  what to show, strategy by strategy (advanced)
///
/// Your catalog (items, users) is synced from your backend with a server SDK and the secret key.
/// A `Likyly` is safe to share across tasks: create one for the app.
public final class Likyly: Sendable {
    public let events: Events
    public let placements: Placements
    public let recommendations: Recommendations

    /// Key prefixes: "sk_" secret, "pk_" public, "lk_" developer (older unprefixed keys keep working).
    static let unsafePrefixes = ["sk_", "lk_"]

    public convenience init(apiKey: String, options: LikylyOptions = LikylyOptions()) throws {
        try self.init(apiKey: apiKey, options: options, sleep: { try await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) },
                      random: { Double.random(in: 0..<1) }, now: { Date() })
    }

    init(apiKey: String, options: LikylyOptions, sleep: @escaping @Sendable (TimeInterval) async throws -> Void,
         random: @escaping @Sendable () -> Double, now: @escaping @Sendable () -> Date) throws {
        if apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            throw validation("apiKey is required (create one in your LIKYLY account)")
        }
        // Anything shipped in an app binary can be extracted from it: a secret or developer key there gives
        // anyone write access to the catalog. Recommendations and events only ever need the public key.
        if Self.unsafePrefixes.contains(where: apiKey.hasPrefix) {
            throw validation("This looks like a secret or developer key. An app ships to visitors' devices, where its keys can be read: " +
                             "use the restricted public key (\"pk_...\", account page's \"Clé publique\") and keep the secret key on your server.")
        }
        var ua = "likyly-swift/\(likylyVersion)"
        if let suffix = options.userAgent, !suffix.isEmpty { ua += " " + suffix }
        let core = Core(apiKey: apiKey, baseURL: options.baseURL, catalog: options.catalog, timeout: options.timeout,
                        maxRetries: options.maxRetries, userAgent: ua, transport: options.transport, sleep: sleep, random: random)
        events = Events(core: core)
        recommendations = Recommendations(core: core)
        placements = Placements(core: core, cache: options.cache.map { RecommendCache($0, now: now) })
    }

    /// Shorthand for `placements.recommend`.
    public func recommend(_ placement: String, context: PlacementContext? = nil, limit: Int? = nil,
                          options: RequestOptions? = nil) async throws -> PlacementRecommendation {
        try await placements.recommend(placement, context: context, limit: limit, options: options)
    }

    /// Forget every cached placement answer (e.g. after "Vider le cache" in the LIKYLY console).
    public func clearRecommendationCache() async {
        await placements.cache?.clear()
    }
}
