import { ValidationError } from "./errors.js";
import { HttpClient } from "./http.js";
import { EventsResource } from "./resources/events.js";
import { ItemsResource } from "./resources/items.js";
import { PlacementsResource } from "./resources/placements.js";
import { RecommendationsResource } from "./resources/recommendations.js";
import { UsersResource } from "./resources/users.js";
import type { PlacementRecommendOptions, PlacementRecommendResponse, RequestOptions } from "./types.js";
import { DEFAULT_BASE_URL, USER_AGENT } from "./version.js";

/** Key prefixes issued from now on (older, unprefixed keys keep working) - "sk_" secret,
 * "pk_" public, "lk_" developer. Lets this SDK refuse a browser-unsafe key at construction
 * time instead of only documenting "don't do this" - see the constructor below. */
const UNSAFE_FOR_BROWSER_PREFIXES = ["sk_", "lk_"];

export interface LikylyOptions {
  /**
   * Your API key. Two kinds: the **secret** key (backend only: items, users, everything) and the
   * **public** key (safe in a web page: recommendations and event tracking only). Never put the
   * secret key in code that ships to a browser.
   */
  apiKey: string;
  /** Defaults to `https://api.likyly.com`. */
  baseUrl?: string;
  /**
   * Which of your catalogs to use, if your account has several. Omit it if you have one: LIKYLY
   * uses your only catalog.
   */
  catalog?: string;
  /** Per-request timeout in milliseconds. Default 10000. */
  timeout?: number;
  /** Automatic retries on transient failures (429, 502, 503, 504, dropped connections). Default 2. Set 0 to disable. */
  maxRetries?: number;
  /** Appended to the SDK's User-Agent, e.g. `my-shop/1.4`. */
  userAgent?: string;
  /** Custom `fetch` (proxies, tests). Defaults to the global one (Node 18+, browsers, edge runtimes). */
  fetch?: typeof fetch;
  /** @internal test hooks */
  _internal?: { sleep?: (ms: number) => Promise<void>; random?: () => number };
}

/**
 * The LIKYLY client. Five things to know:
 *
 *   likyly.items            your catalog
 *   likyly.users            your users (optional)
 *   likyly.events           what visitors do
 *   likyly.recommendations  what to show them (advanced, per-call)
 *   likyly.placements       what to show them (recommended - a named, persistent config)
 */
export class Likyly {
  readonly items: ItemsResource;
  readonly users: UsersResource;
  readonly events: EventsResource;
  readonly recommendations: RecommendationsResource;
  readonly placements: PlacementsResource;

  constructor(options: LikylyOptions) {
    if (!options || typeof options.apiKey !== "string" || options.apiKey.trim() === "") {
      throw new ValidationError("apiKey is required (create one in your LIKYLY account)");
    }
    // A secret or developer key running where a browser could read this process's memory/
    // bundle (window+document present - true in a real browser, false in Node/most edge
    // runtimes) is exactly the mistake this guard exists to catch immediately, rather than
    // silently shipping it. Neither key kind has any reason to run there: recommendations and
    // event tracking (the only things safe in a browser) only ever need the public key.
    const isBrowserLike = typeof (globalThis as { window?: unknown }).window !== "undefined" && typeof (globalThis as { document?: unknown }).document !== "undefined";
    if (isBrowserLike && UNSAFE_FOR_BROWSER_PREFIXES.some((prefix) => options.apiKey.startsWith(prefix))) {
      throw new ValidationError(
        "This looks like a secret or developer key, and this code is running in a browser. " +
          "Never put a secret/developer key where browser-facing code could read it - use the " +
          "restricted public key here instead (account page's \"Clé publique\"), and keep the " +
          "secret/developer key server-side only.",
      );
    }
    const fetchImpl = options.fetch ?? globalThis.fetch?.bind(globalThis);
    if (!fetchImpl) throw new ValidationError("no fetch implementation found: use Node 18+ or pass options.fetch");

    const http = new HttpClient({
      apiKey: options.apiKey,
      baseUrl: (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, ""),
      catalog: options.catalog,
      timeout: options.timeout ?? 10_000,
      maxRetries: options.maxRetries ?? 2,
      userAgent: options.userAgent ? `${USER_AGENT} ${options.userAgent}` : USER_AGENT,
      fetch: fetchImpl,
      sleep: options._internal?.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms))),
      random: options._internal?.random ?? Math.random,
    });

    this.items = new ItemsResource(http);
    this.users = new UsersResource(http);
    this.events = new EventsResource(http);
    this.recommendations = new RecommendationsResource(http);
    this.placements = new PlacementsResource(http);
  }

  /**
   * Shorthand for `likyly.placements.recommend(...)` - recommendations for one placement.
   * Configure the placement once (LIKYLY's MCP admin tools or dashboard), then:
   *
   *     const result = await likyly.recommend({
   *       placement: "pdp-related",
   *       context: { itemId: product.id, userId: user?.id, sessionId },
   *     });
   */
  recommend(opts: PlacementRecommendOptions, options?: RequestOptions): Promise<PlacementRecommendResponse> {
    return this.placements.recommend(opts, options);
  }
}
