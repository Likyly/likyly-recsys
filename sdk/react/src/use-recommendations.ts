"use client";
import { useEffect, useRef, useSyncExternalStore } from "react";
import type { PlacementContext } from "@likyly/sdk";
import { RecommendationsController, type RecommendationsState } from "./controller.js";
import { useLikyly } from "./provider.js";

export interface UseRecommendationsOptions {
  /** The placement's slug, e.g. "pdp-related" - configure it once via the LIKYLY MCP admin
   * tools or dashboard, not from this hook. */
  placement: string;
  context?: PlacementContext;
  /** Overrides the placement's own configured limit for this call. */
  limit?: number;
  /** Skip fetching - e.g. while a signal the placement needs (itemId, userId) isn't known yet. Default true. */
  enabled?: boolean;
}

export interface UseRecommendationsResult extends RecommendationsState {
  /** Re-runs the same request (e.g. a manual "refresh" action). */
  refetch: () => void;
  /** Fire `recommendationImpression` for one of the returned items - manual here, since a
   * headless hook has no DOM for LIKYLY to observe; `<LikylyRecommendations>` does this for you. */
  trackImpression: (itemId: string) => void;
  /** Fire `recommendationClick` for one of the returned items. */
  trackClick: (itemId: string) => void;
}

/**
 * Headless: fetches recommendations for one placement and returns the data plus tracking
 * helpers - you render 100% of the markup. See `<LikylyRecommendations>` for a ready-to-use
 * component that also auto-tracks impressions/clicks.
 *
 * ```tsx
 * const { items, isLoading, trackClick } = useRecommendations({
 *   placement: "pdp-related",
 *   context: { itemId: product.id, userId: user?.id, sessionId },
 * });
 * ```
 */
export function useRecommendations(opts: UseRecommendationsOptions): UseRecommendationsResult {
  const { client } = useLikyly();
  const controllerRef = useRef<RecommendationsController | null>(null);
  if (!controllerRef.current) controllerRef.current = new RecommendationsController(client);
  const controller = controllerRef.current;

  // Third argument (getServerSnapshot) is required for SSR/SSG (Next.js prerendering, ...) -
  // without it React throws ("Missing getServerSnapshot") the moment this hook is reached
  // during a server render. Safe to reuse the same getSnapshot here: run() only ever executes
  // from the effect below, which never runs during a server render, so the controller's state
  // is still the untouched initial one (a stable reference) at that point.
  const state = useSyncExternalStore(
    (onStoreChange) => controller.subscribe(onStoreChange),
    () => controller.getState(),
    () => controller.getState(),
  );

  // Compared by value, not identity: `context` is a fresh object literal on most callers' every
  // render, and re-fetching on every render (rather than on an actual change) would defeat the
  // point of the hook.
  const contextKey = JSON.stringify(opts.context ?? {});
  const runCurrent = () => void controller.run(opts);

  useEffect(() => {
    runCurrent();
    // contextKey stands in for opts.context; opts.placement/limit/enabled are the other real
    // inputs - this intentionally does not depend on the `opts` object identity itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller, opts.placement, contextKey, opts.limit, opts.enabled]);

  return {
    ...state,
    refetch: runCurrent,
    trackImpression: (itemId: string) => controller.trackImpression(itemId),
    trackClick: (itemId: string) => controller.trackClick(itemId),
  };
}
