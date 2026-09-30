import { compact, encodeSegment, requireId } from "../encoding.js";
import { ValidationError } from "../errors.js";
import type { HttpClient } from "../http.js";
import type { EventBatchResult, EventInput, EventResult, IdentifyInput, IdentifyResult, RequestOptions, TypedEventInput } from "../types.js";

/**
 * `likyly.events` - what your visitors do. Works with the **public** key from a browser, or the
 * secret key from your backend. `track()` is the one mechanism; `view()`, `click()`, ... are
 * shortcuts that call it with the matching event type.
 */
export class EventsResource {
  constructor(private readonly http: HttpClient) {}

  /**
   * Records one event of any type: `view`, `click`, ... or your own (`favorite`, `share`, ...).
   * Event types are open strings - the six helpers below are conveniences, not a closed list.
   *
   * Retries: a failed call is only retried automatically when it carries an `eventId` (then a
   * replay is harmless); without one, an ambiguous failure is thrown rather than risking a duplicate.
   */
  async track(type: string, event: EventInput, options?: RequestOptions): Promise<EventResult> {
    const res = await this.http.request<WireEventResult>({
      method: "POST",
      path: `/events/${encodeSegment(requireEventType(type))}`,
      body: eventBody(event),
      idempotent: event.eventId !== undefined,
      options,
    });
    return { message: res.data.message, eventId: res.data.event_id ?? undefined, duplicate: res.data.duplicate === true };
  }

  /** Up to 1000 events in one call, each with its own `type`. All-or-nothing validation. */
  async trackMany(events: TypedEventInput[], options?: RequestOptions): Promise<EventBatchResult> {
    if (!Array.isArray(events) || events.length === 0) throw new ValidationError("events must be a non-empty array");
    const res = await this.http.request<WireBatchResult>({
      method: "POST",
      path: "/events/batch",
      body: { events: events.map((e) => ({ event_type: requireEventType(e.type), ...eventBody(e) })) },
      idempotent: events.every((e) => e.eventId !== undefined),
      options,
    });
    return { received: res.data.received, accepted: res.data.accepted, duplicates: res.data.duplicates };
  }

  /** The item was shown to the visitor (send the `recommendationId` it came with). */
  impression(event: EventInput, options?: RequestOptions): Promise<EventResult> {
    return this.track("impression", event, options);
  }
  /** The visitor looked at the item (a product page, an article). */
  view(event: EventInput, options?: RequestOptions): Promise<EventResult> {
    return this.track("view", event, options);
  }
  /** The visitor clicked the item. */
  click(event: EventInput, options?: RequestOptions): Promise<EventResult> {
    return this.track("click", event, options);
  }
  addToCart(event: EventInput, options?: RequestOptions): Promise<EventResult> {
    return this.track("add_to_cart", event, options);
  }
  removeFromCart(event: EventInput, options?: RequestOptions): Promise<EventResult> {
    return this.track("remove_from_cart", event, options);
  }
  /** Set `eventId` (e.g. `purchase_<orderId>_<itemId>`) so a retry can never count the purchase twice. */
  purchase(event: EventInput, options?: RequestOptions): Promise<EventResult> {
    return this.track("purchase", event, options);
  }

  // ---- Clearer aliases for the recommendation-tracking events ---------------------------
  // Same wire call as impression/view/click above (mapped server-side onto the same
  // already-weighted event type - see db.EVENT_TYPE_ALIASES) - purely a naming preference,
  // e.g. for code a coding agent generates from the LIKYLY MCP tools' own vocabulary.

  /** Alias of {@link impression} - the item was shown to the visitor in a recommendation slot. */
  recommendationImpression(event: EventInput, options?: RequestOptions): Promise<EventResult> {
    return this.track("recommendation_impression", event, options);
  }
  /** Alias of {@link click} - the visitor clicked a recommended item. */
  recommendationClick(event: EventInput, options?: RequestOptions): Promise<EventResult> {
    return this.track("recommendation_click", event, options);
  }
  /** Alias of {@link view} - the visitor looked at a product/article page. */
  productView(event: EventInput, options?: RequestOptions): Promise<EventResult> {
    return this.track("product_view", event, options);
  }

  /**
   * Call right after login/signup, once you know `userId`, for the `sessionId` the visitor
   * was anonymous under: every past interaction of that session not already attributed to a
   * user is reassigned to `userId`, so it counts as that user's history from then on. Safe to
   * call more than once. See `LikylySession.identify` for the client-side counterpart that
   * also remembers the link locally.
   */
  async identify(input: IdentifyInput, options?: RequestOptions): Promise<IdentifyResult> {
    const userId = requireId(input.userId, "userId");
    const sessionId = requireId(input.sessionId, "sessionId");
    const res = await this.http.request<{ linked_interactions: number }>({
      method: "POST",
      path: "/events/identify",
      body: { user_id: userId, session_id: sessionId },
      idempotent: true, // only ever reassigns not-yet-attributed rows - replaying is harmless
      options,
    });
    return { linkedInteractions: res.data.linked_interactions };
  }
}

interface WireEventResult {
  message: string;
  event_id?: string | null;
  duplicate?: boolean;
}
interface WireBatchResult {
  received: number;
  accepted: number;
  duplicates: number;
}

function requireEventType(type: unknown): string {
  if (typeof type !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(type)) {
    throw new ValidationError('event type must be 1-64 characters of letters, digits, "_" or "-" (e.g. "view", "add_to_cart", "favorite")');
  }
  return type;
}

/** `properties` is passed through untouched: its keys are yours. */
function eventBody(event: EventInput): Record<string, unknown> {
  if (event === null || typeof event !== "object") throw new ValidationError("event must be an object");
  const itemId = requireId(event.itemId, "itemId");
  if (event.userId === undefined && event.sessionId === undefined) {
    throw new ValidationError("an event needs a userId or a sessionId (or both)");
  }
  if (event.userId !== undefined) requireId(event.userId, "userId");
  if (event.sessionId !== undefined) requireId(event.sessionId, "sessionId");
  return compact({
    event_id: event.eventId,
    user_id: event.userId,
    session_id: event.sessionId,
    item_id: itemId,
    recommendation_id: event.recommendationId,
    placement: event.placement,
    quantity: event.quantity,
    occurred_at: event.occurredAt instanceof Date ? event.occurredAt.toISOString() : event.occurredAt,
    properties: event.properties,
  });
}
