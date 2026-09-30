// HTTP client for the LIKYLY API surface this MCP server exposes as tools. Two tiers, both
// just forwarding whatever key the process was started with - this client never decides what
// it's allowed to do, the API does:
//  - Recommendations/events (getRecommendations..trackEvent below) - safe with the
//    restricted public key (see the recsys API's get_current_client_id_public_ok scope).
//  - Data sources (listSourceTypes..getCatalogStats below) - needs the secret key or a
//    developer key with the right scope (see /clients/me/developer-keys); a public-key
//    caller gets a clear 401/403 from the API itself.
// Deliberately standalone rather than depending on ../../js (not published to npm yet) - same
// request shape as sdk/js/src/client.ts, trimmed to just this tool surface.

export interface LikylyClientOptions {
  baseUrl: string;
  apiKey: string;
}

type Query = Record<string, string | number | boolean | undefined | null>;

export class LikylyClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;

  constructor(options: LikylyClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.apiKey = options.apiKey;
  }

  /** The recommendation call: LIKYLY picks the strategy. */
  async getRecommendations(
    productType: string,
    request: {
      user_id?: string;
      session_id?: string;
      item_id?: string;
      viewed_item_ids?: string[];
      placement?: string;
      count?: number;
    },
  ) {
    return this.request("POST", "/getRec", { query: { data_product_type: productType }, body: request });
  }

  async getColdStartRecs(productType: string, itemId: string, count = 4) {
    return this.request("GET", `/getRec/content/${encodeURIComponent(itemId)}/${count}`, {
      query: { data_product_type: productType, response_format: "object" },
    });
  }

  async getPopularRecs(productType: string, count = 4) {
    return this.request("GET", `/getRec/popular/${count}`, {
      query: { data_product_type: productType, response_format: "object" },
    });
  }

  async getUserRecs(productType: string, userId: string, count = 3) {
    return this.request("GET", `/getRec/collaborative/${encodeURIComponent(userId)}/${count}`, {
      query: { data_product_type: productType, response_format: "object" },
    });
  }

  async getHybridRecs(productType: string, userId: string, itemId: string, count = 3, alpha = 0.5) {
    return this.request("GET", `/getRec/hybrid/${encodeURIComponent(userId)}/${encodeURIComponent(itemId)}/${count}`, {
      query: { data_product_type: productType, alpha, response_format: "object" },
    });
  }

  async getSessionRecs(productType: string, viewedItemIds: string[], count = 3) {
    return this.request("GET", "/getRec/session", {
      query: { data_product_type: productType, viewed_item_ids: viewedItemIds.join(","), count, response_format: "object" },
    });
  }

  async getUserSessionRecs(productType: string, userId: string, count = 3) {
    return this.request("GET", `/getRec/sessionForUser/${encodeURIComponent(userId)}/${count}`, {
      query: { data_product_type: productType, response_format: "object" },
    });
  }

  // ---------------------------------------------------------------------------------------
  // Data sources - admin surface, only usable with the secret key or a developer key
  // carrying the right scope (see /clients/me/developer-keys). Every call below just
  // forwards to the recsys API's /data-sources* routes; the API is the source of truth for
  // what's allowed - a public-key caller gets a clear 401/403 from it, same as any other
  // tool here would.
  // ---------------------------------------------------------------------------------------

  async listSourceTypes() {
    return this.request("GET", "/data-sources/types");
  }

  async createDataSource(input: {
    name: string;
    type: string;
    product_type: string;
    config?: Record<string, unknown>;
    credentials?: Record<string, unknown>;
    field_mapping?: Record<string, unknown>;
    sync_mode?: string;
  }) {
    return this.request("POST", "/data-sources", { body: input });
  }

  async listDataSources() {
    return this.request("GET", "/data-sources");
  }

  async getDataSource(dataSourceId: number) {
    return this.request("GET", `/data-sources/${dataSourceId}`);
  }

  async testDataSource(dataSourceId: number) {
    return this.request("POST", `/data-sources/${dataSourceId}/test`);
  }

  async previewDataSource(dataSourceId: number, limit?: number) {
    return this.request("POST", `/data-sources/${dataSourceId}/preview`, { query: { limit } });
  }

  async dryRunFieldMapping(dataSourceId: number, mapping: Record<string, unknown>, limit?: number) {
    return this.request("POST", `/data-sources/${dataSourceId}/field-mapping/dry-run`, {
      query: { limit }, body: { mapping },
    });
  }

  async saveFieldMapping(dataSourceId: number, mapping: Record<string, unknown>) {
    return this.request("PUT", `/data-sources/${dataSourceId}/field-mapping`, { body: { mapping } });
  }

  async syncDataSource(dataSourceId: number, mode?: string) {
    return this.request("POST", `/data-sources/${dataSourceId}/sync`, { body: { mode: mode ?? "incremental" } });
  }

  async listSyncs(dataSourceId: number, limit?: number) {
    return this.request("GET", `/data-sources/${dataSourceId}/syncs`, { query: { limit } });
  }

  async getCatalogStats(dataSourceId: number) {
    return this.request("GET", `/data-sources/${dataSourceId}/stats`);
  }

  // ---------------------------------------------------------------------------------------
  // Placements - the recommended orchestration abstraction over the strategies above. Admin
  // calls (all but recommend) need the secret key or a developer key with placements:read/
  // placements:write; the actual PlacementRecommendResponse/Request shapes are documented in
  // docs/placements.md, not repeated here.
  // ---------------------------------------------------------------------------------------

  async listPlacements() {
    return this.request("GET", "/placements");
  }

  async createPlacement(input: Record<string, unknown>) {
    return this.request("POST", "/placements", { body: input });
  }

  async getPlacement(slug: string) {
    return this.request("GET", `/placements/${encodeURIComponent(slug)}`);
  }

  async updatePlacement(slug: string, input: Record<string, unknown>) {
    return this.request("PATCH", `/placements/${encodeURIComponent(slug)}`, { body: input });
  }

  async deactivatePlacement(slug: string) {
    return this.request("POST", `/placements/${encodeURIComponent(slug)}/deactivate`);
  }

  async deletePlacement(slug: string) {
    return this.request("DELETE", `/placements/${encodeURIComponent(slug)}`);
  }

  async getPlacementRequirements(slug: string) {
    return this.request("GET", `/placements/${encodeURIComponent(slug)}/requirements`);
  }

  async previewPlacement(slug: string, context: Record<string, unknown>, limit?: number) {
    return this.request("POST", `/placements/${encodeURIComponent(slug)}/preview`, { body: { context, limit } });
  }

  async getTrackingRequirements(slug: string) {
    return this.request("GET", `/placements/${encodeURIComponent(slug)}/tracking-requirements`);
  }

  async getPlacementHealth(slug: string, windowHours?: number) {
    return this.request("GET", `/placements/${encodeURIComponent(slug)}/health`, { query: { window_hours: windowHours } });
  }

  async getRecentIntegrationEvents(slug: string, windowHours?: number, limit?: number) {
    return this.request("GET", `/placements/${encodeURIComponent(slug)}/recent-events`, { query: { window_hours: windowHours, limit } });
  }

  /** impression, view, click, add_to_cart, remove_from_cart, purchase - or any type of your own. */
  async trackEvent(
    productType: string,
    eventType: string,
    event: {
      item_id: string;
      user_id?: string;
      session_id?: string;
      recommendation_id?: string;
      placement?: string;
      quantity?: number;
      properties?: Record<string, unknown>;
      event_id?: string;
    },
  ) {
    return this.request("POST", `/events/${encodeURIComponent(eventType)}`, {
      query: { data_product_type: productType },
      body: event,
    });
  }

  private async request(method: string, path: string, opts: { query?: Query; body?: unknown } = {}) {
    const url = new URL(this.baseUrl + path);
    if (opts.query) {
      for (const [key, value] of Object.entries(opts.query)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
      }
    }

    const headers: Record<string, string> = { "X-API-Key": this.apiKey };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";

    const response = await fetch(url.toString(), {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });

    const text = await response.text();
    const data = text ? JSON.parse(text) : null;

    if (!response.ok) {
      const detail =
        data && typeof data === "object" && "detail" in data
          ? String((data as Record<string, unknown>).detail)
          : response.statusText;
      throw new Error(`LIKYLY API error (${response.status}): ${detail}`);
    }

    return data;
  }
}
