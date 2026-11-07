// A SwiftUI product screen showing a LIKYLY placement, with the events LIKYLY learns from.
// Copy into an app target that depends on the `Likyly` package.
import Likyly
import SwiftUI

enum Recs {
    // The PUBLIC key ("pk_..."): it can only read recommendations and record events.
    static let likyly = try! Likyly(apiKey: "pk_live_xxx")
    static let session = LikylySession()
}

struct RelatedProducts: View {
    let productId: String
    let userId: String?          // nil while the visitor is not signed in
    let open: (String) -> Void   // your navigation

    @State private var result: PlacementRecommendation?
    @State private var seen = Set<String>()

    var body: some View {
        Group {
            if let result, !result.items.isEmpty {
                ScrollView(.horizontal) {
                    LazyHStack {
                        ForEach(result.items) { item in
                            Button(item.title ?? item.itemId) {
                                track { try await Recs.likyly.events.click(event(item.itemId, result)) }
                                open(item.itemId)
                            }
                            .onAppear {
                                // One impression per card actually shown.
                                guard seen.insert(item.itemId).inserted else { return }
                                track { try await Recs.likyly.events.impression(event(item.itemId, result)) }
                            }
                        }
                    }
                }
            } else {
                EmptyView() // your own fallback (bestsellers, nothing, ...)
            }
        }
        .task(id: productId) {
            seen = []
            track { try await Recs.likyly.events.view(EventInput(itemId: productId, userId: userId, sessionId: Recs.session.anonymousId)) }
            do {
                result = try await Recs.likyly.recommend(
                    "pdp-related",
                    context: PlacementContext(itemId: productId, userId: userId, sessionId: Recs.session.anonymousId),
                    limit: 8)
            } catch {
                result = nil // LIKYLY unreachable, paused or misconfigured: the screen keeps its fallback
            }
        }
    }

    private func event(_ itemId: String, _ result: PlacementRecommendation) -> EventInput {
        EventInput(itemId: itemId, userId: userId, sessionId: Recs.session.anonymousId,
                   recommendationId: result.recommendationId, placement: result.placement)
    }

    /// Events never block or break the screen.
    private func track(_ send: @escaping @Sendable () async throws -> EventResult) {
        Task { _ = try? await send() }
    }
}

// At sign-in, attach the anonymous history to the account:
//     try await Recs.session.identify(Recs.likyly, userId: user.id)
