import { compact, encodeSegment, requireId, requirePathSafeId } from "../encoding.js";
import { ValidationError } from "../errors.js";
import type { HttpClient } from "../http.js";
import type {
  CollaborativeOptions,
  Explanation,
  HybridOptions,
  PopularOptions,
  RecommendationRequest,
  RecommendationResponse,
  RecommendedItem,
  RequestOptions,
  SessionOptions,
  SimilarOptions,
} from "../types.js";

const DEFAULT_LIMIT = 10;

/**
 * `likyly.recommendations`. Use {@link get}: send what you know and LIKYLY picks the best strategy.
 * The other methods are the *Advanced Recommendations* - one strategy at a time.
 */
export class RecommendationsResource {
  constructor(private readonly http: HttpClient) {}

  /**
   * Recommendations for a user, an anonymous session, an item being viewed, viewed items - or
   * nothing at all (then you get what is popular). You never choose the algorithm; `strategy` in the
   * response says what was used. Send `recommendationId` back on the events that follow.
   */
  async get(request: RecommendationRequest = {}, options?: RequestOptions): Promise<RecommendationResponse> {
    const res = await this.http.request<WireRecommendation>({
      method: "POST",
      path: "/getRec",
      body: compact({
        user_id: request.userId !== undefined ? requireId(request.userId, "userId") : undefined,
        session_id: request.sessionId !== undefined ? requireId(request.sessionId, "sessionId") : undefined,
        item_id: request.itemId !== undefined ? requireId(request.itemId, "itemId") : undefined,
        viewed_item_ids: request.viewedItemIds?.map((id) => requireId(id, "viewedItemIds[]")),
        placement: request.placement,
        count: request.limit,
        debug: request.debug,
      }),
      idempotent: true, // a read: repeating it only mints another recommendationId
      options,
    });
    return toRecommendation(res.data);
  }

  // ---- Advanced Recommendations ------------------------------------------------------------
  // One strategy at a time, for expert use. Ids travel in the URL path here, so they cannot contain "/".

  /** The most popular items - the fallback for a visitor with no history at all. */
  async popular(opts: PopularOptions = {}, options?: RequestOptions): Promise<RecommendationResponse> {
    return this.advanced(`/getRec/popular/${limitOf(opts)}`, opts, {}, options);
  }

  /** Items similar to one item (content similarity). No user needed. */
  async similar(opts: SimilarOptions, options?: RequestOptions): Promise<RecommendationResponse> {
    return this.advanced(`/getRec/content/${encodeSegment(requirePathSafeId(opts.itemId, "itemId"))}/${limitOf(opts)}`, opts, {}, options);
  }

  /** Collaborative filtering: what users with similar histories liked. Needs a trained model. */
  async collaborative(opts: CollaborativeOptions, options?: RequestOptions): Promise<RecommendationResponse> {
    return this.advanced(`/getRec/collaborative/${encodeSegment(requirePathSafeId(opts.userId, "userId"))}/${limitOf(opts)}`, opts, {}, options);
  }

  /** Similar items, personalized for a user. `alpha` (0-1) weighs the collaborative signal against content similarity. */
  async hybrid(opts: HybridOptions, options?: RequestOptions): Promise<RecommendationResponse> {
    const path = `/getRec/hybrid/${encodeSegment(requirePathSafeId(opts.userId, "userId"))}/${encodeSegment(requirePathSafeId(opts.itemId, "itemId"))}/${limitOf(opts)}`;
    return this.advanced(path, opts, { alpha: opts.alpha }, options);
  }

  /**
   * Recency-weighted recommendations from what was viewed: pass `viewedItemIds` (an explicit list,
   * oldest first) **or** `userId` (LIKYLY's own history of that user's views) - exactly one.
   */
  async session(opts: SessionOptions, options?: RequestOptions): Promise<RecommendationResponse> {
    const hasList = opts.viewedItemIds !== undefined;
    const hasUser = opts.userId !== undefined;
    if (hasList === hasUser) throw new ValidationError("session() needs either viewedItemIds or userId (exactly one)");
    if (hasUser) {
      return this.advanced(`/getRec/sessionForUser/${encodeSegment(requirePathSafeId(opts.userId, "userId"))}/${limitOf(opts)}`, opts, {}, options);
    }
    const ids = (opts.viewedItemIds ?? []).map((id) => requireId(id, "viewedItemIds[]"));
    if (ids.length === 0) throw new ValidationError("viewedItemIds must contain at least one item id");
    if (ids.some((id) => id.includes(","))) {
      throw new ValidationError('an item id containing "," cannot be sent in this endpoint\'s comma-separated list - use recommendations.get()');
    }
    return this.advanced("/getRec/session", opts, { viewed_item_ids: ids.join(","), count: limitOf(opts) }, options);
  }

  private async advanced(
    path: string,
    opts: { placement?: string; sessionId?: string },
    extra: Record<string, string | number | undefined>,
    options?: RequestOptions,
  ): Promise<RecommendationResponse> {
    const res = await this.http.request<WireRecommendation>({
      method: "GET",
      path,
      // response_format=object: always the same {recommendation_id, strategy, items} envelope
      query: { response_format: "object", placement: opts.placement, session_id: opts.sessionId, ...extra },
      idempotent: true,
      options,
    });
    return toRecommendation(res.data);
  }
}

function limitOf(opts: { limit?: number }): number {
  const limit = opts.limit ?? DEFAULT_LIMIT;
  if (!Number.isInteger(limit) || limit < 1) throw new ValidationError("limit must be a positive integer");
  return limit;
}

// ---- wire format -----------------------------------------------------------------------------
// Exported: resources/placements.ts shares this exact item/explanation shape (LIKYLY's
// RecommendedItem is the same public schema whether it came from /getRec or a placement's
// /recommend) - one mapping to keep in sync, not two.

export interface WireExplanation {
  reason: string;
  content_similarity?: number | null;
  semantic_similarity?: number | null;
  popularity_score?: number | null;
  interaction_count?: number | null;
  interaction_label?: string | null;
  collaborative_score?: number | null;
  source_item_ids?: string[] | null;
  similar_users?: { user_id: string; shared_item_ids?: string[] }[] | null;
}
export interface WireRecommendedItem {
  item_id: string;
  score?: number | null;
  title?: string | null;
  description?: string | null;
  properties?: Record<string, unknown>;
  explanation?: WireExplanation | null;
}
interface WireRecommendation {
  recommendation_id: string;
  strategy: string;
  placement?: string | null;
  items: WireRecommendedItem[];
}

const opt = <T>(v: T | null | undefined): T | undefined => (v === null || v === undefined ? undefined : v);

export function toExplanation(w: WireExplanation): Explanation {
  return {
    reason: w.reason,
    contentSimilarity: opt(w.content_similarity),
    semanticSimilarity: opt(w.semantic_similarity),
    popularityScore: opt(w.popularity_score),
    interactionCount: opt(w.interaction_count),
    interactionLabel: opt(w.interaction_label),
    collaborativeScore: opt(w.collaborative_score),
    sourceItemIds: opt(w.source_item_ids),
    similarUsers: w.similar_users?.map((u) => ({ userId: u.user_id, sharedItemIds: u.shared_item_ids ?? [] })),
  };
}

export function toRecommendedItem(w: WireRecommendedItem): RecommendedItem {
  return {
    itemId: w.item_id,
    score: opt(w.score),
    title: opt(w.title),
    description: opt(w.description),
    properties: w.properties ?? {},
    explanation: w.explanation ? toExplanation(w.explanation) : undefined,
  };
}

function toRecommendation(w: WireRecommendation): RecommendationResponse {
  return {
    recommendationId: w.recommendation_id,
    strategy: w.strategy,
    placement: opt(w.placement),
    items: w.items.map(toRecommendedItem),
  };
}
