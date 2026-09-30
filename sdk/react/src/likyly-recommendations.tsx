"use client";
import { useEffect, useRef, type ReactNode } from "react";
import type { RecommendedItem } from "@likyly/sdk";
import { useRecommendations } from "./use-recommendations.js";

export interface LikylyRecommendationsProps {
  /** The placement's slug, e.g. "pdp-related". */
  placement: string;
  itemId?: string;
  userId?: string;
  sessionId?: string;
  anonymousId?: string;
  categoryId?: string;
  collectionId?: string;
  cartItemIds?: string[];
  limit?: number;
  /** Skip fetching - e.g. while `itemId`/`userId` aren't known yet. Default true. */
  enabled?: boolean;
  /** Applied to the wrapping `<ul>` - the only styling hook when using the default markup. */
  className?: string;
  /**
   * Full control over each card's markup - this component still fetches the data and
   * auto-tracks impression/click, you own every pixel of the card itself. `onClick` fires
   * `recommendationClick`; wire it to whatever element the visitor actually interacts with.
   */
  renderItem?: (item: RecommendedItem, helpers: { onClick: () => void }) => ReactNode;
  /** Rendered while the first request is in flight. */
  loadingFallback?: ReactNode;
  /** Rendered on a request error, or once loaded with zero items. Nothing, by default - a
   * recommendations block that has nothing to show should usually just not be there. */
  emptyFallback?: ReactNode;
}

/**
 * Ready-to-use: fetches a placement's recommendations and renders them, auto-tracking
 * `recommendationImpression` (via `IntersectionObserver` - fired when a card is actually seen,
 * not merely fetched) and `recommendationClick`. Needs a `<LikylyProvider>` above it in the tree.
 *
 * ```tsx
 * <LikylyRecommendations placement="pdp-related" itemId={product.id} userId={user?.id} limit={4} />
 * ```
 *
 * `product_view`/`add_to_cart`/`purchase` are never tracked here - see docs/placements.md for
 * why (this component can't reliably know when your own checkout completes) and the SDK
 * helpers (`likyly.events.productView`/`addToCart`/`purchase`) for instrumenting them yourself.
 */
export function LikylyRecommendations({
  placement,
  itemId,
  userId,
  sessionId,
  anonymousId,
  categoryId,
  collectionId,
  cartItemIds,
  limit,
  enabled,
  className,
  renderItem,
  loadingFallback = null,
  emptyFallback = null,
}: LikylyRecommendationsProps) {
  const { items, isLoading, error, trackImpression, trackClick } = useRecommendations({
    placement,
    context: { itemId, userId, sessionId, anonymousId, categoryId, collectionId, cartItemIds },
    limit,
    enabled,
  });

  if (isLoading && items.length === 0) return <>{loadingFallback}</>;
  if (error || items.length === 0) return <>{emptyFallback}</>;

  return (
    <ul className={className} data-likyly-placement={placement}>
      {items.map((item) => (
        <RecommendationCard
          key={item.itemId}
          item={item}
          renderItem={renderItem}
          onImpression={() => trackImpression(item.itemId)}
          onClick={() => trackClick(item.itemId)}
        />
      ))}
    </ul>
  );
}

function RecommendationCard({
  item,
  renderItem,
  onImpression,
  onClick,
}: {
  item: RecommendedItem;
  renderItem?: LikylyRecommendationsProps["renderItem"];
  onImpression: () => void;
  onClick: () => void;
}) {
  const ref = useRef<HTMLLIElement>(null);
  const trackedImpression = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || trackedImpression.current) return;
    // No IntersectionObserver (very old browsers, some SSR-adjacent test environments): fire
    // once eagerly rather than never tracking the impression at all.
    if (typeof IntersectionObserver === "undefined") {
      trackedImpression.current = true;
      onImpression();
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting && !trackedImpression.current) {
            trackedImpression.current = true;
            onImpression();
            observer.disconnect();
          }
        }
      },
      { threshold: 0.5 },
    );
    observer.observe(el);
    return () => observer.disconnect();
    // onImpression is re-created each render (it closes over `item`) but always calls the same
    // underlying tracked item - re-subscribing on every render would still only ever fire once
    // thanks to trackedImpression, so omitting it from deps is deliberate, not a bug.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <li ref={ref} data-likyly-item-id={item.itemId} onClick={onClick}>
      {renderItem ? (
        renderItem(item, { onClick })
      ) : (
        <a href={typeof item.properties.url === "string" ? item.properties.url : undefined}>
          {typeof item.properties.image === "string" ? <img src={item.properties.image} alt={item.title ?? ""} /> : null}
          <div>{item.title}</div>
          {item.properties.price !== undefined ? <div>{String(item.properties.price)}</div> : null}
        </a>
      )}
    </li>
  );
}
