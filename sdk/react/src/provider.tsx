"use client";
import { createContext, useContext, useMemo, type ReactNode } from "react";
import { Likyly, LikylySession, type LikylyOptions } from "@likyly/sdk";

export interface LikylyProviderProps extends Pick<LikylyOptions, "apiKey" | "baseUrl" | "catalog"> {
  children: ReactNode;
  /** Namespaces the anonymous-session localStorage key - see @likyly/sdk's LikylySession. */
  sessionStorageKey?: string;
}

export interface LikylyContextValue {
  client: Likyly;
  session: LikylySession;
}

const LikylyContext = createContext<LikylyContextValue | undefined>(undefined);

/**
 * Wrap your app (or the part of it that shows recommendations) once. `apiKey` must be your
 * **restricted public key** - this runs in the browser, and `Likyly`'s own constructor refuses
 * a secret/developer-shaped key there (see @likyly/sdk's README, "Which key?"). Never read it
 * from a server-only env var by mistake.
 *
 * ```tsx
 * <LikylyProvider apiKey={process.env.NEXT_PUBLIC_LIKYLY_KEY!}>
 *   <ProductPage />
 * </LikylyProvider>
 * ```
 */
export function LikylyProvider({ apiKey, baseUrl, catalog, sessionStorageKey, children }: LikylyProviderProps) {
  const value = useMemo<LikylyContextValue>(
    () => ({
      client: new Likyly({ apiKey, baseUrl, catalog, userAgent: "react" }),
      session: new LikylySession({ storageKey: sessionStorageKey }),
    }),
    // apiKey/baseUrl/catalog/sessionStorageKey are configuration, not per-render values - a real
    // change (e.g. switching accounts at runtime) intentionally creates a fresh client/session.
    [apiKey, baseUrl, catalog, sessionStorageKey],
  );

  return <LikylyContext.Provider value={value}>{children}</LikylyContext.Provider>;
}

/** The `Likyly` client and `LikylySession` this component tree's `<LikylyProvider>` holds -
 * what `useRecommendations`/`<LikylyRecommendations>` use internally; call it yourself for
 * anything else the SDK offers (`client.events.identify`, `session.getAnonymousId()`, ...). */
export function useLikyly(): LikylyContextValue {
  const value = useContext(LikylyContext);
  if (!value) {
    throw new Error("useLikyly (and useRecommendations / <LikylyRecommendations>) must be used inside <LikylyProvider>");
  }
  return value;
}
