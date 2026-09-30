/** Free-form JSON object: price, currency, orderId, category, any field of your own. Keys are stored exactly as you send them. */
export type Properties = Record<string, unknown>;

// ---- Items -----------------------------------------------------------------------------------

export interface Item {
  /** Your own identifier - any string (`SKU-123`, a UUID, `gid://shopify/Product/123`). */
  itemId: string;
  title: string;
  description?: string;
  properties: Properties;
}

/** What you send to create or replace an item. Only `title` is required. */
export interface ItemInput {
  title: string;
  description?: string;
  /** `category` and `description` feed content similarity; everything else is stored as is. */
  properties?: Properties;
}

export interface ItemImport extends ItemInput {
  itemId: string;
}

export interface ListOptions {
  /** Page size (1-1000, default 100). */
  limit?: number;
  /** Rows to skip. */
  offset?: number;
}

export interface ItemList {
  items: Item[];
  /** Total number of items in the catalog (`X-Total-Count`), when the API reported it. */
  total?: number;
  /** The page size you asked for (undefined = the API's default). */
  limit?: number;
  offset: number;
}

// ---- Users -----------------------------------------------------------------------------------

export interface User {
  userId: string;
  properties: Properties;
}

export interface UserInput {
  /** country, segment, language, ... - whatever describes your users. */
  properties?: Properties;
}

export interface UserImport extends UserInput {
  userId: string;
}

export interface UserList {
  users: User[];
  total?: number;
  limit?: number;
  offset: number;
}

/** Result of a batch call. A failing entry is reported in `errors`, it does not throw. */
export interface BatchResult {
  received: number;
  succeeded: number;
  failed: number;
  errors: { index: number; id?: string; message: string }[];
}

// ---- Events ----------------------------------------------------------------------------------

/**
 * An interaction. `itemId` and at least one of `userId` / `sessionId` are required; sending both
 * ties an anonymous session to the user.
 */
export interface EventInput {
  itemId: string;
  userId?: string;
  /** Anonymous visitor / browsing session - no login needed. */
  sessionId?: string;
  /** The `recommendationId` of the recommendation that surfaced this item - enables attribution. */
  recommendationId?: string;
  /** Free-form label of where this happened: `homepage`, `product_page`, `cart`, ... */
  placement?: string;
  quantity?: number;
  /** ISO 8601 string or Date. Defaults to the server's time. */
  occurredAt?: string | Date;
  properties?: Properties;
  /**
   * Idempotency key, unique per account: replaying an event with the same `eventId` records nothing
   * and returns `duplicate: true`. Set it on purchases - it makes retries safe.
   */
  eventId?: string;
}

/** An event of any type, for `events.trackMany`. `type` is an open string (`view`, `favorite`, ...). */
export interface TypedEventInput extends EventInput {
  type: string;
}

export interface EventResult {
  message: string;
  eventId?: string;
  /** True if this `eventId` was already recorded - nothing was written. */
  duplicate: boolean;
}

export interface EventBatchResult {
  received: number;
  accepted: number;
  /** Events skipped because their `eventId` was already recorded. */
  duplicates: number;
}

/** Call right after login/signup, once you know `userId`, for the `sessionId` the visitor was
 * anonymous under - see `events.identify`. */
export interface IdentifyInput {
  userId: string;
  sessionId: string;
}

export interface IdentifyResult {
  /** How many of this session's past interactions now belong to `userId`. */
  linkedInteractions: number;
}

// ---- Recommendations -------------------------------------------------------------------------

/** Send whatever you know: LIKYLY chooses the best strategy. All fields are optional. */
export interface RecommendationRequest {
  userId?: string;
  sessionId?: string;
  /** The item being looked at (e.g. the product page). */
  itemId?: string;
  /** Recently viewed items, oldest first. */
  viewedItemIds?: string[];
  /** Free-form label of where the recommendations will be shown. */
  placement?: string;
  /** How many items to return (1-100, default 10). */
  limit?: number;
  /** Diagnostic detail in `explanation`. Secret key only. */
  debug?: boolean;
}

export interface SimilarUser {
  userId: string;
  sharedItemIds: string[];
}

export interface Explanation {
  reason: string;
  contentSimilarity?: number;
  semanticSimilarity?: number;
  popularityScore?: number;
  interactionCount?: number;
  interactionLabel?: string;
  collaborativeScore?: number;
  sourceItemIds?: string[];
  /** Only with `debug` and a secret key. */
  similarUsers?: SimilarUser[];
}

export interface RecommendedItem {
  itemId: string;
  score?: number;
  title?: string;
  description?: string;
  properties: Properties;
  explanation?: Explanation;
}

export interface RecommendationResponse {
  /** Send it back on the impression / click / add_to_cart / purchase events for these items. */
  recommendationId: string;
  /** What LIKYLY used: `hybrid`, `content`, `collaborative`, `session` or `popular`. */
  strategy: string;
  placement?: string;
  items: RecommendedItem[];
}

interface AdvancedBase {
  placement?: string;
  /** Attach the recommendation to an anonymous session, for attribution. */
  sessionId?: string;
  /** How many items to return (default 10). */
  limit?: number;
}

export interface PopularOptions extends AdvancedBase {}
export interface SimilarOptions extends AdvancedBase {
  itemId: string;
}
export interface CollaborativeOptions extends AdvancedBase {
  userId: string;
}
export interface HybridOptions extends AdvancedBase {
  userId: string;
  itemId: string;
  /** Weight of the collaborative signal against content similarity: 0 = pure content, 1 = pure collaborative (default 0.5). */
  alpha?: number;
}
/** Give `viewedItemIds` (an explicit list, oldest first) OR `userId` (LIKYLY's own history of that user's views). */
export interface SessionOptions extends AdvancedBase {
  viewedItemIds?: string[];
  userId?: string;
}

// ---- Placements --------------------------------------------------------------------------------
// The recommended way to ask for recommendations - configure a Placement once (LIKYLY's MCP
// admin tools, or the dashboard - not from this runtime SDK) and call it by `slug` here on.
// See docs/placements.md in the LIKYLY repo.

/**
 * What you know about the moment - every field optional unless the placement's own
 * `signals.required` names it (see `likyly.placements` and GET .../requirements).
 */
export interface PlacementContext {
  /** The product/article/listing currently being viewed. */
  itemId?: string;
  categoryId?: string;
  collectionId?: string;
  /** Identified visitor. */
  userId?: string;
  /** Stable anonymous visitor id - used like `sessionId` when `sessionId` is omitted. See `LikylySession`. */
  anonymousId?: string;
  /** Anonymous browsing session. */
  sessionId?: string;
  cartItemIds?: string[];
  locale?: string;
  customContext?: Record<string, unknown>;
}

export interface PlacementRecommendOptions {
  /** The placement's slug, e.g. "pdp-related". */
  placement: string;
  context?: PlacementContext;
  /** Overrides the placement's own configured limit for this call. */
  limit?: number;
}

export interface PlacementRecommendResponse {
  /** Send it back on the impression / click / add_to_cart / purchase events for these items. */
  recommendationId: string;
  placement: string;
  /** What the placement actually used: `hybrid`, `content`, `collaborative`, `session` or `popular`. */
  strategyUsed: string;
  items: RecommendedItem[];
}

// ---- Per-call options ------------------------------------------------------------------------

export interface RequestOptions {
  /** Cancel the call. */
  signal?: AbortSignal;
  /** Overrides the client's timeout (milliseconds) for this call. */
  timeout?: number;
  /** Overrides the client's retry count for this call. */
  maxRetries?: number;
}
