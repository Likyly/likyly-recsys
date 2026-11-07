import Foundation

/// A first-party anonymous visitor id for the app: generated once ("anon_<uuid>") and kept in
/// `UserDefaults`, so a visitor who never signs in still gets recommendations from their own history. Use it
/// as `sessionId` on events and `anonymousId` / `sessionId` in a placement's context, then call `identify`
/// once the visitor signs in.
///
/// It is not an advertising identifier (no IDFA, nothing shared with other apps), so it needs no App
/// Tracking Transparency prompt; declare "Product Interaction" data linked to the user in your privacy label.
public final class LikylySession: @unchecked Sendable {
    public static let defaultStorageKey = "likyly_anonymous_id"
    private let defaults: UserDefaults
    private let storageKey: String
    private let lock = NSLock()

    public init(defaults: UserDefaults = .standard, storageKey: String = LikylySession.defaultStorageKey) {
        self.defaults = defaults
        self.storageKey = storageKey
    }

    /// The visitor's anonymous id, created and persisted on first use.
    public var anonymousId: String {
        lock.lock()
        defer { lock.unlock() }
        if let id = defaults.string(forKey: storageKey), !id.isEmpty { return id }
        let id = "anon_" + UUID().uuidString.lowercased()
        defaults.set(id, forKey: storageKey)
        return id
    }

    /// Call once you know the visitor's `userId` (right after sign-in or sign-up): attaches this session's
    /// past interactions to them. Safe to call more than once.
    @discardableResult
    public func identify(_ likyly: Likyly, userId: String, options: RequestOptions? = nil) async throws -> IdentifyResult {
        try await likyly.events.identify(userId: userId, sessionId: anonymousId, options: options)
    }

    /// Forget the anonymous id (e.g. at sign-out): the next visitor on this device starts a new one.
    public func reset() {
        lock.lock()
        defer { lock.unlock() }
        defaults.removeObject(forKey: storageKey)
    }
}
