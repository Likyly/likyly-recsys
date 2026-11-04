import { compact, encodeSegment, requireId } from "../encoding.js";
import type { HttpClient } from "../http.js";
import type { PlacementContext, PlacementRecommendOptions, PlacementRecommendResponse, RequestOptions } from "../types.js";
import { toRecommendedItem, type WireRecommendedItem } from "./recommendations.js";
import { LikylyError, NetworkError, PausedError, RateLimitError, TimeoutError } from "../errors.js";
import type { RecommendCache } from "../recommend-cache.js";

/**
 * `likyly.placements` - the recommended way to ask for recommendations. A Placement ("what to
 * recommend, where, with what fallback and filters") is configured once - via the LIKYLY MCP
 * admin tools or dashboard, never from this runtime SDK - and called by `slug` from here on.
 * `likyly.recommendations` (the strategy-specific / signal-based API) remains available for
 * advanced, per-call use with no persistent configuration.
 */
export class PlacementsResource {
  constructor(
    private readonly http: HttpClient,
    /** null when the client was built with `cache: false`. */
    readonly cache: RecommendCache | null = null,
  ) {}

  /**
   * Recommendations for one placement: send whatever context you have (`itemId`, `userId`,
   * `sessionId`, ...) - the placement's own configuration decides the strategy, fallback and
   * filters, not you. Send `recommendationId` back on the events that follow to attribute them.
   */
  async recommend(opts: PlacementRecommendOptions, options?: RequestOptions): Promise<PlacementRecommendResponse> {
    const placement = requireId(opts.placement, "placement");
    const body = compact({ context: contextBody(opts.context), limit: opts.limit });
    const key = this.cache ? cacheKey(placement, body) : "";
    const cached = this.cache?.get(key);
    if (cached) return cached;
    if (this.cache?.isPaused()) {
      throw new PausedError(`LIKYLY did not answer a moment ago: calls resume in ${this.cache.pauseRemaining()}s`);
    }
    try {
      const res = await this.http.request<WirePlacementRecommendation>({
        method: "POST",
        path: `/placements/${encodeSegment(placement)}/recommend`,
        body,
        // A read in effect, but each call mints a fresh recommendationId server-side - repeating
        // it on retry is exactly what you'd want for a dropped connection, not a duplicate write.
        idempotent: true,
        options,
      });
      const result = toPlacementRecommendation(res.data);
      this.cache?.recordSuccess();
      this.cache?.set(key, result);
      return result;
    } catch (error) {
      if (isOutage(error)) this.cache?.recordFailure();
      throw error;
    }
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

/** Same placement, limit and context = same answer; who the anonymous visitor is does not count. */
function cacheKey(placement: string, body: Record<string, unknown>): string {
  const context = { ...((body.context as Record<string, unknown> | undefined) ?? {}) };
  delete context.session_id;
  delete context.anonymous_id;
  const sorted = Object.fromEntries(Object.entries(context).sort(([a], [b]) => a.localeCompare(b)));
  return `${placement}|${body.limit ?? ""}|${JSON.stringify(sorted)}`;
}

/** LIKYLY down or overloaded, as opposed to a mistake in the call (4xx). */
function isOutage(error: unknown): boolean {
  if (error instanceof NetworkError || error instanceof TimeoutError || error instanceof RateLimitError) return true;
  return error instanceof LikylyError && (error.statusCode ?? 0) >= 500;
}
