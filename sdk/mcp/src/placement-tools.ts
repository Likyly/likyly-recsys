import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { LikylyClient } from "./client.js";

// Placement tools: the recommended way to wire LIKYLY recommendations into a site. A
// Placement is a named, persistent config ("what to recommend, where, with what fallback and
// filters") - create one, preview it, then the site calls it by slug from
// POST /placements/{slug}/recommend (not an MCP tool - that's a runtime HTTP call the site's
// own code makes, safe with the public key). These tools need the secret key or a developer
// key with placements:read/placements:write - a caller using the wrong key gets a clear
// 401/403 back through the tool result, same as the data-source admin tools.
//
// No LLM call happens in LIKYLY for any of this: turning a developer's natural-language
// request ("4 related products on a PDP, session-based for anonymous visitors...") into the
// parameters below is exactly what the coding agent calling these tools already does -
// that's the point of exposing deterministic primitives instead of another prompt layer.
export function createLikylyPlacementTools(server: McpServer, client: LikylyClient): void {
  function textResult(data: unknown) {
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  }

  const slug = z.string().min(1).max(128).describe("The placement's slug, e.g. 'pdp-related'.");
  const strategy = z.enum(["auto", "content", "session", "collaborative", "hybrid", "popular"]);
  const contextType = z.enum([
    "product_page", "listing_page", "category_page", "homepage", "cart", "account", "content_page", "custom",
  ]);
  const signalName = z.enum([
    "current_item_id", "category_id", "collection_id", "user_id", "anonymous_id",
    "session_id", "cart_item_ids", "locale", "custom_context",
  ]);
  const signals = z.object({
    required: z.array(signalName).default([]).describe("Context keys this placement can't run without - missing one is a clear error, never a silent guess."),
    optional: z.array(signalName).default([]).describe("Context keys this placement uses when present, but doesn't need."),
  }).describe("Every context key is optional to send unless listed in `required`.");
  const attributeRule = z.object({ attribute: z.string(), values: z.array(z.unknown()).min(1) });
  const filters = z.object({
    category_in: z.array(z.string()).optional(),
    category_not_in: z.array(z.string()).optional(),
    in_stock_only: z.boolean().optional().describe("Only filters items that actually have a stock/in_stock property - never excludes one for lacking it."),
    include_attributes: z.array(attributeRule).optional(),
    exclude_attributes: z.array(attributeRule).optional(),
  });
  const businessRules = z.object({
    exclude_current_item: z.boolean().optional().describe("Default true - never recommend the item the visitor is already looking at back to itself."),
    exclude_cart_items: z.boolean().optional().describe("Never recommend an item already in the cart - use for a cart cross-sell placement."),
  });
  const context = z.record(z.unknown()).describe(
    "Sample context to run the placement against - any of current_item_id, category_id, collection_id, user_id, anonymous_id, session_id, cart_item_ids, locale, custom_context.",
  );

  server.tool(
    "list_placements",
    "Every placement configured for this account. Check this before creating a new one, or to find a slug by name.",
    {},
    async () => textResult(await client.listPlacements()),
  );

  server.tool(
    "get_placement",
    "One placement's full configuration by slug.",
    { slug },
    async ({ slug }) => textResult(await client.getPlacement(slug)),
  );

  server.tool(
    "create_placement",
    "Registers a new named recommendation configuration - the abstraction to reach for instead of picking a raw strategy endpoint. Underneath it always calls one of LIKYLY's existing engines (content/session/collaborative/hybrid/popular) - 'auto' delegates to the same signal-based selection POST /getRec already uses (hybrid when both a user and an item are known, content from just an item, session from recent views, collaborative for a known user with a trained model, popular otherwise) - it is not a new ML model, just an orchestration choice. Set `signals.required` to the context keys this placement can't run without (e.g. current_item_id for a product-page placement) - everything else stays optional. `fallback_strategy` is tried if `strategy` returns nothing; 'popular' is always the final safety net regardless. Call preview_placement right after creating it.",
    {
      slug,
      name: z.string().min(1).max(200),
      context_type: contextType.default("custom"),
      product_type: z.string().optional().describe("Catalog namespace to recommend from - omit it when this account has a single catalog."),
      limit: z.number().int().min(1).max(100).default(10),
      audience: z.enum(["all", "anonymous", "identified"]).default("all"),
      strategy: strategy.default("auto"),
      signals: signals.default({ required: [], optional: [] }),
      fallback_strategy: strategy.optional().describe("Tried if `strategy` returns no results. 'popular' is always the final safety net regardless."),
      filters: filters.default({}),
      business_rules: businessRules.default({}),
      enabled: z.boolean().default(true),
    },
    async (input) => textResult(await client.createPlacement(input)),
  );

  server.tool(
    "update_placement",
    "Partial update of an existing placement - only the fields given are changed. Sending `signals`/`filters`/`business_rules` at all replaces that whole sub-object (not a deep merge). Use this to disable/re-enable too (`enabled: true|false`) - or delete_or_disable_placement's default mode, which is the same thing plus a clearer name for it.",
    {
      slug,
      name: z.string().optional(),
      context_type: contextType.optional(),
      product_type: z.string().optional(),
      limit: z.number().int().min(1).max(100).optional(),
      audience: z.enum(["all", "anonymous", "identified"]).optional(),
      strategy: strategy.optional(),
      signals: signals.optional(),
      fallback_strategy: strategy.optional(),
      filters: filters.optional(),
      business_rules: businessRules.optional(),
      enabled: z.boolean().optional(),
    },
    async ({ slug, ...input }) => textResult(await client.updatePlacement(slug, input)),
  );

  server.tool(
    "delete_or_disable_placement",
    "mode:'disable' (the default, reversible): POST /placements/{slug}/recommend starts 404ing immediately, nothing is deleted - prefer this. mode:'delete': permanent, only for a configuration that was wrong and shouldn't exist at all.",
    { slug, mode: z.enum(["disable", "delete"]).default("disable") },
    async ({ slug, mode }) => textResult(mode === "delete" ? await client.deletePlacement(slug) : await client.deactivatePlacement(slug)),
  );

  server.tool(
    "get_placement_requirements",
    "Static introspection (no engine call) - the required/optional context signals, strategy, fallback_strategy and context_type. Call this before wiring a site to a placement, to know exactly what context (current_item_id, user_id, session_id, ...) to send at recommend time.",
    { slug },
    async ({ slug }) => textResult(await client.getPlacementRequirements(slug)),
  );

  server.tool(
    "preview_placement",
    "Runs a placement's full orchestration (strategy selection, fallback, filters) against a sample context - never writes anything, mints no recommendation_id, nothing attributable. Returns strategy_used, items, whether a fallback was used, which strategies were attempted, which context signals were actually present, and warnings/errors (a missing required signal shows up in `errors`, not a thrown error) - use this to validate a placement before wiring it into a site, or to debug one that isn't behaving as expected.",
    { slug, context: context.default({}), limit: z.number().int().min(1).max(100).optional() },
    async ({ slug, context, limit }) => textResult(await client.previewPlacement(slug, context, limit)),
  );
}
