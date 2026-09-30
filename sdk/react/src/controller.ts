import type { Likyly, PlacementContext, RecommendedItem } from "@likyly/sdk";

/**
 * Framework-agnostic state machine behind `useRecommendations` - kept separate from any React
 * API on purpose: it's what makes the hook's fetch/race-condition/tracking logic testable with
 * plain Node tests, no DOM or React renderer needed (see test/controller.test.ts). The hook
 * itself (`use-recommendations.ts`) is a thin `useSyncExternalStore` wrapper around one instance
 * of this per component.
 */
export interface RecommendationsControllerOptions {
  placement: string;
  context?: PlacementContext;
  limit?: number;
  /** Skip fetching - e.g. while a signal the placement needs (itemId, userId) isn't known yet. */
  enabled?: boolean;
}

export interface RecommendationsState {
  items: RecommendedItem[];
  strategyUsed?: string;
  recommendationId?: string;
  isLoading: boolean;
  error?: Error;
}

const INITIAL_STATE: RecommendationsState = { items: [], isLoading: false };

type TrackableLikyly = Pick<Likyly, "recommend" | "events">;

export class RecommendationsController {
  private state: RecommendationsState = INITIAL_STATE;
  private readonly listeners = new Set<() => void>();
  private lastPlacement: string | undefined;
  private lastContext: PlacementContext | undefined;
  // Bumped on every run() - a response for a superseded call (placement/context changed while
  // it was in flight) is dropped instead of clobbering a newer, already-resolved state.
  private generation = 0;

  constructor(private readonly likyly: TrackableLikyly) {}

  getState(): RecommendationsState {
    return this.state;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private setState(patch: Partial<RecommendationsState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }

  async run(opts: RecommendationsControllerOptions): Promise<void> {
    this.lastPlacement = opts.placement;
    this.lastContext = opts.context;
    if (opts.enabled === false) {
      this.generation += 1; // invalidate any call still in flight from before enabled became false
      this.setState(INITIAL_STATE);
      return;
    }

    const generation = ++this.generation;
    this.setState({ isLoading: true, error: undefined });
    try {
      const result = await this.likyly.recommend({ placement: opts.placement, context: opts.context, limit: opts.limit });
      if (generation !== this.generation) return; // a newer call already started - drop this result
      this.setState({ items: result.items, strategyUsed: result.strategyUsed, recommendationId: result.recommendationId, isLoading: false });
    } catch (error) {
      if (generation !== this.generation) return;
      this.setState({ isLoading: false, error: error instanceof Error ? error : new Error(String(error)) });
    }
  }

  /** `recommendationImpression` for one rendered item, attributed to the last `recommend()`
   * call - manual in headless mode (there's no DOM for LIKYLY to observe); `<LikylyRecommendations>`
   * calls this automatically via IntersectionObserver. A no-op before the first result arrives,
   * or if neither a user nor a session was in the context (nothing to attribute the event to). */
  trackImpression(itemId: string): void {
    this.trackRecommendationEvent("recommendationImpression", itemId);
  }

  /** `recommendationClick` for one item - same attribution/no-op rules as {@link trackImpression}. */
  trackClick(itemId: string): void {
    this.trackRecommendationEvent("recommendationClick", itemId);
  }

  private trackRecommendationEvent(method: "recommendationImpression" | "recommendationClick", itemId: string): void {
    const { recommendationId } = this.state;
    if (!recommendationId || !this.lastPlacement) return;
    const userId = this.lastContext?.userId;
    const sessionId = this.lastContext?.sessionId ?? this.lastContext?.anonymousId;
    if (!userId && !sessionId) return; // an event needs a user or a session - see @likyly/sdk's EventInput
    void this.likyly.events[method]({ itemId, recommendationId, placement: this.lastPlacement, userId, sessionId });
  }
}
