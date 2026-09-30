import type { Likyly } from "./client.js";
import type { IdentifyResult } from "./types.js";

const DEFAULT_STORAGE_KEY = "likyly_anonymous_id";

export interface LikylySessionOptions {
  /** Override the localStorage key - e.g. to namespace multiple LIKYLY-powered apps on one domain. Defaults to "likyly_anonymous_id". */
  storageKey?: string;
}

/**
 * A first-party, cookieless anonymous visitor id - generated once per browser and persisted in
 * `localStorage` (never a third-party cookie, never sent to anyone but LIKYLY). Use it as
 * `context.anonymousId` / `sessionId` for recommendations and event tracking until the visitor
 * is identified, then call {@link identify} to attach their history to the now-known user.
 *
 * SSR-safe: every method degrades to a no-op (`undefined`) outside a real browser (no
 * `window`/`localStorage`, private-mode storage that throws, ...) rather than throwing - a
 * server-rendered pass simply has no anonymous id yet, which is correct, not an error.
 */
export class LikylySession {
  private readonly storageKey: string;
  private cachedId: string | undefined;

  constructor(options: LikylySessionOptions = {}) {
    this.storageKey = options.storageKey ?? DEFAULT_STORAGE_KEY;
  }

  /** The visitor's anonymous id, creating and persisting one on first call. `undefined` when
   * there is nowhere to persist it (SSR, Node, storage disabled). */
  getAnonymousId(): string | undefined {
    if (this.cachedId) return this.cachedId;
    const storage = safeLocalStorage();
    if (!storage) return undefined;
    try {
      let id = storage.getItem(this.storageKey);
      if (!id) {
        id = generateAnonymousId();
        storage.setItem(this.storageKey, id);
      }
      this.cachedId = id;
      return id;
    } catch {
      return undefined; // full/blocked storage - degrade to "no session", never throw
    }
  }

  /**
   * Call once you know the visitor's `userId` (e.g. right after login): attaches this
   * session's *past* interactions to them server-side (`likyly.events.identify`), so they
   * count as that user's history from then on. Safe to call more than once, and safe even if
   * there is no anonymous id yet (a no-op). Both `userId` and the anonymous id remain fine to
   * keep sending together afterwards - this doesn't need to be the last time you use either.
   */
  async identify(likyly: Pick<Likyly, "events">, userId: string): Promise<IdentifyResult | undefined> {
    const sessionId = this.getAnonymousId();
    if (!sessionId) return undefined;
    return likyly.events.identify({ userId, sessionId });
  }
}

function safeLocalStorage(): Storage | undefined {
  try {
    const storage = (globalThis as { localStorage?: Storage }).localStorage;
    if (!storage) return undefined;
    const probeKey = "__likyly_probe__";
    storage.setItem(probeKey, "1"); // some private-mode browsers only throw on write, not on presence
    storage.removeItem(probeKey);
    return storage;
  } catch {
    return undefined;
  }
}

function generateAnonymousId(): string {
  const cryptoObj = (globalThis as { crypto?: Crypto }).crypto;
  if (typeof cryptoObj?.randomUUID === "function") return `anon_${cryptoObj.randomUUID()}`;
  return `anon_${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}
