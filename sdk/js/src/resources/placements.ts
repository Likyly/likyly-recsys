import { compact, encodeSegment, requireId } from "../encoding.js";
import type { HttpClient } from "../http.js";
import type { PlacementContext, PlacementRecommendOptions, PlacementRecommendResponse, RequestOptions } from "../types.js";
import { toRecommendedItem, type WireRecommendedItem } from "./recommendations.js";

/**
 * `likyly.placements` - the recommended way to ask for recommendations. A Placement ("what to
 * recommend, where, with what fallback and filters") is configured once - via the LIKYLY MCP
 * admin tools or dashboard, never from this runtime SDK - and called by `slug` from here on.
 * `likyly.recommendations` (the strategy-specific / signal-based API) remains available for
 * advanced, per-call use with no persistent configuration.
 */
export class PlacementsResource {
  constructor(private readonly http: HttpClient) {}

  /**
   * Recommendations for one placement: send whatever context you have (`itemId`, `userId`,
   * `sessionId`, ...) - the placement's own configuration decides the strategy, fallback and
   * filters, not you. Send `recommendationId` back on the events that follow to attribute them.
   */
  async recommend(opts: PlacementRecommendOptions, options?: RequestOptions): Promise<PlacementRecommendResponse> {
    const placement = requireId(opts.placement, "placement");
    const res = await this.http.request<WirePlacementRecommendation>({
      method: "POST",
      path: `/placements/${encodeSegment(placement)}/recommend`,
      body: compact({ context: contextBody(opts.context), limit: opts.limit }),
      // A read in effect, but each call mints a fresh recommendationId server-side - repeating
      // it on retry is exactly what you'd want for a dropped connection, not a duplicate write.
      idempotent: true,
      options,
    });
    return toPlacementRecommendation(res.data);
  }
}

function contextBody(context: PlacementContext | undefined): Record<string, unknown> | undefined {
  if (!context) return undefined;
  return compact({
    current_item_id: context.itemId,
    category_id: context.categoryId,
    collection_id: context.collectionId,
    user_id: context.userId,
    anonymous_id: context.anonymousId,
    session_id: context.sessionId,
    cart_item_ids: context.cartItemIds,
    locale: context.locale,
    custom_context: context.customContext,
  });
}

// ---- wire format -----------------------------------------------------------------------------

interface WirePlacementRecommendation {
  recommendation_id: string;
  placement: string;
  strategy_used: string;
  items: WireRecommendedItem[];
}

function toPlacementRecommendation(w: WirePlacementRecommendation): PlacementRecommendResponse {
  return {
    recommendationId: w.recommendation_id,
    placement: w.placement,
    strategyUsed: w.strategy_used,
    items: w.items.map(toRecommendedItem),
  };
}
