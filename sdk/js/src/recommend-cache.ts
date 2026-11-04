/**
 * Client-side protection for `placements.recommend`, so a page does not call LIKYLY on every
 * view and does not keep hammering it when it is down:
 *
 * - a cache: an answer is reused for `ttlSeconds` (default one hour) for the same placement,
 *   item and visitor. In a browser it lives in `sessionStorage` (one visitor's tab session:
 *   back/forward and revisits cost nothing); elsewhere (SSR, Node) in memory. A reused answer
 *   keeps its `recommendationId`, so impressions and clicks stay attributed.
 * - a circuit breaker: after a network error, a timeout or a 5xx, calls fail at once for
 *   `pauseSeconds` (default 30) instead of waiting on LIKYLY, so the page shows its own
 *   fallback straight away. The next call after the pause tries again.
 */
import type { PlacementRecommendResponse } from "./types.js";

export interface RecommendCacheOptions {
  /** How long an answer is reused, in seconds. Default 3600. */
  ttlSeconds?: number;
  /** After LIKYLY fails, how long to skip calls, in seconds. Default 30. Set 0 to always call. */
  pauseSeconds?: number;
  /** Where answers are kept. Default: "session" in a browser (sessionStorage), "memory" elsewhere. */
  storage?: "session" | "memory";
}

const PREFIX = "likyly:rec:";
const MAX_MEMORY_ENTRIES = 500;

interface Entry {
  expires: number;
  value: PlacementRecommendResponse;
}

function browserStorage(): Storage | null {
  try {
    const storage = (globalThis as { sessionStorage?: Storage }).sessionStorage;
    if (!storage) return null;
    storage.getItem(PREFIX); // throws where storage is blocked (privacy settings, sandboxed frames)
    return storage;
  } catch {
    return null;
  }
}

export class RecommendCache {
  private readonly ttlMs: number;
  private readonly pauseMs: number;
  private readonly storage: Storage | null;
  private readonly memory = new Map<string, Entry>();
  private pausedUntil = 0;

  constructor(options: RecommendCacheOptions = {}, private readonly now: () => number = Date.now) {
    this.ttlMs = Math.max(0, options.ttlSeconds ?? 3600) * 1000;
    this.pauseMs = Math.max(0, options.pauseSeconds ?? 30) * 1000;
    this.storage = options.storage === "memory" ? null : browserStorage();
  }

  get(key: string): PlacementRecommendResponse | null {
    if (this.ttlMs === 0) return null;
    const entry = this.read(key);
    if (!entry) return null;
    if (entry.expires <= this.now()) {
      this.remove(key);
      return null;
    }
    return entry.value;
  }

  set(key: string, value: PlacementRecommendResponse): void {
    if (this.ttlMs === 0) return;
    const entry: Entry = { expires: this.now() + this.ttlMs, value };
    if (this.storage) {
      try {
        this.storage.setItem(PREFIX + key, JSON.stringify(entry));
        return;
      } catch {
        // Storage full or blocked: fall back to memory for this one.
      }
    }
    this.memory.set(key, entry);
    if (this.memory.size > MAX_MEMORY_ENTRIES) this.memory.delete(this.memory.keys().next().value as string);
  }

  /** Drop every cached answer of this client (e.g. after a purge in the LIKYLY console). */
  clear(): void {
    this.memory.clear();
    if (!this.storage) return;
    try {
      for (let i = this.storage.length - 1; i >= 0; i--) {
        const k = this.storage.key(i);
        if (k?.startsWith(PREFIX)) this.storage.removeItem(k);
      }
    } catch {
      // Blocked storage: nothing was written there either.
    }
  }

  /** True while calls are paused after a failure. */
  isPaused(): boolean {
    return this.now() < this.pausedUntil;
  }

  /** Seconds left in the pause (0 when not paused). */
  pauseRemaining(): number {
    return Math.max(0, Math.ceil((this.pausedUntil - this.now()) / 1000));
  }

  recordFailure(): void {
    if (this.pauseMs > 0) this.pausedUntil = this.now() + this.pauseMs;
  }

  recordSuccess(): void {
    this.pausedUntil = 0;
  }

  private read(key: string): Entry | null {
    if (this.storage) {
      try {
        const raw = this.storage.getItem(PREFIX + key);
        if (raw) return JSON.parse(raw) as Entry;
      } catch {
        // Unreadable entry or storage: treat as a miss.
      }
    }
    return this.memory.get(key) ?? null;
  }

  private remove(key: string): void {
    this.memory.delete(key);
    try {
      this.storage?.removeItem(PREFIX + key);
    } catch {
      // ignore
    }
  }
}
