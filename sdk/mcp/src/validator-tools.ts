import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { LikylyClient } from "./client.js";

// The integration validator: real facts from LIKYLY's own data (recent recommend calls,
// impressions, clicks, product views, add-to-carts, purchases), not a guess from reading the
// site's source. "Vérifie que mon intégration Likyly est correcte" should be answerable by
// calling validate_integration, not by Claude Code re-reading its own previous edits.
export function createLikylyValidatorTools(server: McpServer, client: LikylyClient): void {
  function textResult(text: string) {
    return { content: [{ type: "text" as const, text }] };
  }
  function jsonResult(data: unknown) {
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  }

  const slug = z.string().min(1).max(128).describe("The placement's slug.");
  const windowHours = z.number().int().min(1).max(24 * 30).default(24).describe("How far back to look, in hours. Default 24.");

  server.tool(
    "get_tracking_requirements",
    "Which events this placement needs instrumented, and why - call this before writing tracking code (recommendation_impression/recommendation_click are placement-specific; product_view/add_to_cart/purchase are catalog-wide funnel events every placement benefits from). Follow up with get_placement_health once the site is live to confirm they actually arrived.",
    { slug },
    async ({ slug }) => jsonResult(await client.getTrackingRequirements(slug)),
  );

  server.tool(
    "get_placement_health",
    "Structured counts of what actually arrived for this placement in the last window_hours (recommend calls, impressions, clicks, product views, add-to-carts, purchases), each marked ok/warning/missing. The raw data behind validate_integration - use that tool instead for a human-readable checklist; use this one if you need the numbers themselves.",
    { slug, window_hours: windowHours },
    async ({ slug, window_hours }) => jsonResult(await client.getPlacementHealth(slug, window_hours)),
  );

  server.tool(
    "get_recent_integration_events",
    "The individual recent recommend calls and tracked events behind get_placement_health's counts - for debugging exactly which item/session/strategy was involved, when the aggregate counts alone don't explain what's wrong (e.g. recommendations were requested but always for the wrong item_id).",
    { slug, window_hours: windowHours, limit: z.number().int().min(1).max(100).default(20) },
    async ({ slug, window_hours, limit }) => jsonResult(await client.getRecentIntegrationEvents(slug, window_hours, limit)),
  );

  server.tool(
    "validate_integration",
    "Step 11 of wiring up a placement: verifies the integration is actually working, from LIKYLY's own data - not a guess from reading the site's source. Call this after rendering the placement and adding tracking, whenever a developer asks to \"check\"/\"verify\" their LIKYLY integration. Renders a checklist (✓ ok, ⚠ received but worth a look, ✗ missing/critical) and, for anything not ok, a concrete next step - loop back to get_integration_recipe or get_tracking_requirements for the ones it names.",
    { slug, window_hours: windowHours },
    async ({ slug, window_hours }) => textResult(await renderValidation(client, slug, window_hours)),
  );
}

const CHECK_LABELS: Record<string, { ok: string; bad: string; nextStep: string }> = {
  recommendations_requested: {
    ok: "recommendations requested",
    bad: "recommendations not requested yet",
    nextStep: "Call POST /placements/{slug}/recommend (likyly.recommend(...) in the SDK) from the page - nothing else here can be true until this is.",
  },
  current_item_supplied: {
    ok: "current item supplied",
    bad: "current item not supplied",
    nextStep: "Pass context.itemId (the product/article being viewed) to likyly.recommend / useRecommendations - this placement requires it.",
  },
  anonymous_session_supplied: {
    ok: "anonymous session supplied",
    bad: "anonymous session not supplied",
    nextStep: "Pass context.sessionId (or anonymousId - see LikylySession) for anonymous visitors - this placement requires one.",
  },
  impressions_received: {
    ok: "impressions received",
    bad: "impressions not received",
    nextStep: "Fire recommendationImpression for each rendered item (automatic with <LikylyRecommendations>; call trackImpression yourself with useRecommendations).",
  },
  recommendation_clicks_received: {
    ok: "recommendation clicks received",
    bad: "recommendation clicks not received",
    nextStep: "Fire recommendationClick when a recommended item is clicked (automatic with <LikylyRecommendations>; call trackClick yourself with useRecommendations). Can legitimately be zero on low traffic.",
  },
  product_views_received: {
    ok: "product views received",
    bad: "product views not received",
    nextStep: "Call likyly.events.productView on your product/article page - this is a catalog-wide event, not specific to this placement.",
  },
  add_to_cart_received: {
    ok: "add_to_cart received",
    bad: "add_to_cart not received",
    nextStep: "Call likyly.events.addToCart where the visitor adds an item to their cart - optional but improves ranking quality over time.",
  },
  purchases_received: {
    ok: "purchases received",
    bad: "purchases not received",
    nextStep: "Call likyly.events.purchase (with an eventId, so a retry never double-counts a sale) once checkout completes - the strongest training signal, and what attribution ultimately measures.",
  },
};

const SYMBOL: Record<string, string> = { ok: "✓", warning: "⚠", missing: "✗" };

async function renderValidation(client: LikylyClient, slug: string, windowHours: number): Promise<string> {
  const health = (await client.getPlacementHealth(slug, windowHours)) as {
    overall: string;
    checks: { name: string; status: "ok" | "warning" | "missing"; count: number }[];
  };

  const lines = [`Placement: ${slug}`, ""];
  const nextSteps: string[] = [];
  for (const check of health.checks) {
    const labels = CHECK_LABELS[check.name] ?? { ok: check.name, bad: `${check.name} (not received)`, nextStep: "" };
    const symbol = SYMBOL[check.status] ?? "?";
    lines.push(`${symbol} ${check.status === "ok" ? labels.ok : labels.bad}`);
    if (check.status !== "ok" && labels.nextStep) nextSteps.push(`- ${labels.bad}: ${labels.nextStep}`);
  }

  if (nextSteps.length > 0) {
    lines.push("", "Next steps:", ...nextSteps);
  } else {
    lines.push("", "Everything this validator checks is flowing - the integration looks correct.");
  }
  return lines.join("\n");
}
