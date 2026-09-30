import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { createLikylyAdminTools } from "./admin-tools.js";
import { createLikylyPlacementTools } from "./placement-tools.js";
import { createLikylyRecipeTools } from "./recipe-tools.js";
import { createLikylyValidatorTools } from "./validator-tools.js";
import type { LikylyClient } from "./client.js";

// Shared tool surface: used by the local stdio server (index.ts) and the hosted HTTP
// service (http.ts). Two tiers of tools, both just forwarding whatever key this server was
// started with - the recommendation/event tools below work with the restricted public key;
// the admin tools (list_source_types..get_catalog_stats, in admin-tools.ts) configure and run
// catalog data-source integrations, and need the secret key or a developer key with the
// right scope instead (see /clients/me/developer-keys in the recsys API) - a caller using
// the wrong key gets a clear 401/403 back through the tool result, same as any other error
// here. Registering both unconditionally (rather than trying to detect the key's own
// capabilities up front) keeps this server simple and lets the API stay the single source of
// truth for what a given key can do.
export function createLikylyServer(client: LikylyClient, defaultProductType?: string): McpServer {
  const server = new McpServer({ name: "likyly-recsys", version: "0.2.0" });

  const productType = defaultProductType
    ? z.string().optional().describe(`Catalog namespace. Defaults to "${defaultProductType}" if omitted.`)
    : z.string().describe("Catalog namespace, e.g. \"movies\" or your own product category.");

  function resolveProductType(value: string | undefined): string {
    const resolved = value ?? defaultProductType;
    if (!resolved) {
      throw new Error(
        "No product_type given and LIKYLY_PRODUCT_TYPE is not set in the server's environment.",
      );
    }
    return resolved;
  }

  function textResult(data: unknown) {
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  }

  const id = z.string().min(1).max(255);

  server.tool(
    "get_recommendations",
    "The recommendation call to use: send whatever you know (a user_id, an anonymous session_id, the item_id being viewed, viewed_item_ids, a placement label) and LIKYLY picks the best strategy (hybrid, content, collaborative, session or popular) and returns it in `strategy`. Every response has a `recommendation_id` - pass it as recommendation_id to track_event for the impression/click/add_to_cart/purchase events that follow, to attribute them. Ids are YOUR own ids (any string).",
    {
      product_type: productType,
      user_id: id.optional(),
      session_id: id.optional().describe("Anonymous visitor / browsing session id."),
      item_id: id.optional().describe("The item being looked at."),
      viewed_item_ids: z.array(id).max(50).optional().describe("Recently viewed item ids, oldest first."),
      placement: z.string().max(128).optional().describe("Free-form label of where the recommendations are shown, e.g. homepage."),
      count: z.number().int().min(1).max(100).default(10),
    },
    async ({ product_type, ...request }) => textResult(await client.getRecommendations(resolveProductType(product_type), request)),
  );

  server.tool(
    "get_popular_recommendations",
    "Most popular products (pure popularity ranking) - the right fallback for a visitor with no browsing history and no product to anchor content similarity on.",
    { product_type: productType, count: z.number().int().min(1).max(50).default(10) },
    async ({ product_type, count }) => textResult(await client.getPopularRecs(resolveProductType(product_type), count)),
  );

  server.tool(
    "get_cold_start_recommendations",
    "Content-based recommendations similar to one product (synopsis/genre/description similarity + semantic embeddings) - works with zero user history, e.g. on a product page.",
    { product_type: productType, item_id: id, count: z.number().int().min(1).max(50).default(4) },
    async ({ product_type, item_id, count }) =>
      textResult(await client.getColdStartRecs(resolveProductType(product_type), item_id, count)),
  );

  server.tool(
    "get_collaborative_recommendations",
    "Collaborative-filtering recommendations (implicit ALS) for a known/logged-in user, learned from cross-user purchase patterns.",
    { product_type: productType, user_id: id, count: z.number().int().min(1).max(50).default(3) },
    async ({ product_type, user_id, count }) =>
      textResult(await client.getUserRecs(resolveProductType(product_type), user_id, count)),
  );

  server.tool(
    "get_hybrid_recommendations",
    "Blends content similarity to one product with collaborative filtering for one user. alpha (0-1) weights collaborative vs. content: 0 = pure content, 1 = pure collaborative, 0.5 = balanced.",
    {
      product_type: productType,
      user_id: id,
      item_id: id,
      count: z.number().int().min(1).max(50).default(3),
      alpha: z.number().min(0).max(1).default(0.5),
    },
    async ({ product_type, user_id, item_id, count, alpha }) =>
      textResult(await client.getHybridRecs(resolveProductType(product_type), user_id, item_id, count, alpha)),
  );

  server.tool(
    "get_session_recommendations",
    "Recency-weighted recommendations from an explicit list of recently-viewed item ids - no account needed (anonymous session). The last id in the list is weighted the most.",
    { product_type: productType, viewed_item_ids: z.array(id).min(1).max(50), count: z.number().int().min(1).max(50).default(3) },
    async ({ product_type, viewed_item_ids, count }) =>
      textResult(await client.getSessionRecs(resolveProductType(product_type), viewed_item_ids, count)),
  );

  server.tool(
    "get_user_session_recommendations",
    "Same recency-weighted logic as get_session_recommendations, but sourced from a logged-in user's persisted view history instead of a client-supplied list - survives across devices/sessions.",
    { product_type: productType, user_id: id, count: z.number().int().min(1).max(50).default(3) },
    async ({ product_type, user_id, count }) =>
      textResult(await client.getUserSessionRecs(resolveProductType(product_type), user_id, count)),
  );

  server.tool(
    "track_event",
    "Record an interaction: impression, view, click, add_to_cart, remove_from_cart, purchase - or any event type of your own. Needs item_id and at least one of user_id (logged in) / session_id (anonymous). Pass the recommendation_id of the get_recommendations call that surfaced the item to attribute it. Send event_id (any unique string) on purchases so a retry is not counted twice.",
    {
      product_type: productType,
      event_type: z.string().regex(/^[a-zA-Z0-9_-]+$/).max(64).describe("e.g. view, click, purchase"),
      item_id: id,
      user_id: id.optional(),
      session_id: id.optional(),
      recommendation_id: z.string().max(128).optional(),
      placement: z.string().max(128).optional(),
      quantity: z.number().int().min(0).optional(),
      properties: z.record(z.unknown()).optional().describe("Free-form JSON: price, currency, order_id, revenue, ..."),
      event_id: z.string().max(255).optional(),
    },
    async ({ product_type, event_type, ...event }) => {
      if (!event.user_id && !event.session_id) throw new Error("Provide user_id and/or session_id.");
      return textResult(await client.trackEvent(resolveProductType(product_type), event_type, event));
    },
  );

  createLikylyAdminTools(server, client);
  createLikylyPlacementTools(server, client);
  createLikylyRecipeTools(server, client);
  createLikylyValidatorTools(server, client);

  return server;
}
