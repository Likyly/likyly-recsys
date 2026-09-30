import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { LikylyClient } from "./client.js";

// get_integration_recipe: a structured contract for wiring a placement into a real codebase -
// packages to install, env vars, the public API to call, the context/events it needs, a short
// illustrative snippet, and warnings. Deliberately NOT a whole file to paste: `framework` /
// `renderingMode` / `hasAuthContext` are supplied by the calling agent (it already inspected
// the repo) - LIKYLY never tries to detect a customer's stack itself, and this tool composes
// its answer from that input plus the placement's own live requirements (get_placement_requirements),
// not from guessing.
export function createLikylyRecipeTools(server: McpServer, client: LikylyClient): void {
  function jsonResult(data: unknown) {
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  }

  const framework = z.enum(["react", "nextjs", "vue", "svelte", "vanilla-js", "other"]);

  server.tool(
    "get_integration_recipe",
    "Everything needed to wire one placement into a real codebase, as structured data - not a file to copy-paste. Call this after create_placement (and, ideally, after preview_placement/configure_field_mapping have confirmed it produces sane results). Tell it the framework you found in the project (you inspected it - this tool never guesses), and it returns: packages to install, env vars, the public API to call for this framework, the context/events this specific placement needs, a short illustrative snippet, and warnings (e.g. the placement is disabled, or no auth context was given so userId will be omitted).",
    {
      placement: z.string().min(1).max(128),
      framework,
      rendering_mode: z.enum(["csr", "ssr", "rsc"]).optional().describe("For react/nextjs: whether the integration point is a client component, a server-rendered page, or a React Server Component."),
      has_auth_context: z.boolean().default(false).describe("Does the project already expose the current user's id at the point this placement will render?"),
    },
    async ({ placement, framework, rendering_mode, has_auth_context }) => {
      const [requirements, tracking] = await Promise.all([
        client.getPlacementRequirements(placement) as Promise<{
          slug: string; enabled: boolean; strategy: string; fallback_strategy: string | null;
          context_type: string; signals: { required: string[]; optional: string[] };
        }>,
        client.getTrackingRequirements(placement) as Promise<{ required_events: { event_type: string; scope: string; why: string }[] }>,
      ]);
      return jsonResult(buildRecipe(requirements, tracking, framework, rendering_mode, has_auth_context));
    },
  );
}

interface Recipe {
  packages: string[];
  env: { name: string; value: string }[];
  publicApi: string;
  requiredContext: { name: string; required: boolean }[];
  requiredEvents: { eventType: string; scope: string; why: string }[];
  minimalExample: string;
  warnings: string[];
}

function buildRecipe(
  requirements: { slug: string; enabled: boolean; strategy: string; fallback_strategy: string | null; context_type: string; signals: { required: string[]; optional: string[] } },
  tracking: { required_events: { event_type: string; scope: string; why: string }[] },
  framework: "react" | "nextjs" | "vue" | "svelte" | "vanilla-js" | "other",
  renderingMode: "csr" | "ssr" | "rsc" | undefined,
  hasAuthContext: boolean,
): Recipe {
  const isReactFamily = framework === "react" || framework === "nextjs";
  const packages = isReactFamily ? ["@likyly/sdk", "@likyly/react"] : ["@likyly/sdk"];

  const env = [
    {
      name: framework === "nextjs" ? "NEXT_PUBLIC_LIKYLY_KEY" : framework === "vue" || framework === "svelte" ? "VITE_PUBLIC_LIKYLY_KEY" : "LIKYLY_PUBLIC_KEY",
      value: "the account's restricted PUBLIC key only - never the secret or a developer key (see docs/placements.md's Security note)",
    },
  ];

  const requiredContext = [
    ...requirements.signals.required.map((name) => ({ name, required: true })),
    ...requirements.signals.optional.map((name) => ({ name, required: false })),
  ];

  const requiredEvents = tracking.required_events.map((e) => ({ eventType: e.event_type, scope: e.scope, why: e.why }));

  const publicApi = isReactFamily
    ? "@likyly/react: <LikylyProvider apiKey> once at the app root, then useRecommendations({placement, context}) or <LikylyRecommendations placement .../> where the placement renders."
    : "@likyly/sdk: new Likyly({apiKey}) once, then likyly.recommend({placement, context}) where the placement renders, and likyly.events.* for tracking.";

  const minimalExample = isReactFamily
    ? [
        framework === "nextjs" ? '"use client";' : undefined,
        `import { LikylyRecommendations } from "@likyly/react";`,
        "",
        `<LikylyRecommendations placement="${requirements.slug}"`,
        requirements.signals.required.includes("current_item_id") || requirements.signals.optional.includes("current_item_id") ? "  itemId={product.id}" : undefined,
        requirements.signals.required.includes("user_id") || requirements.signals.optional.includes("user_id") ? "  userId={user?.id}" : undefined,
        "/>",
      ]
        .filter((line): line is string => line !== undefined)
        .join("\n")
    : [
        `import { Likyly } from "@likyly/sdk";`,
        `const likyly = new Likyly({ apiKey: /* public key */ });`,
        "",
        `const result = await likyly.recommend({ placement: "${requirements.slug}", context: { itemId: product.id, userId: user?.id, sessionId } });`,
        `// render result.items yourself, then:`,
        `await likyly.events.recommendationImpression({ itemId: result.items[0].itemId, recommendationId: result.recommendationId, placement: "${requirements.slug}", userId: user?.id, sessionId });`,
      ].join("\n");

  const warnings: string[] = [];
  if (!requirements.enabled) {
    warnings.push(`Placement "${requirements.slug}" is currently disabled (enabled: false) - recommend() will 404 until it's re-enabled (update_placement / delete_or_disable_placement with mode "disable" toggles this).`);
  }
  if (!hasAuthContext && (requirements.signals.required.includes("user_id") || requirements.signals.optional.includes("user_id"))) {
    warnings.push("No auth context given - userId will be omitted at the integration point until the project's own auth state is wired in; this placement will rely on session-based signals alone until then.");
  }
  if (isReactFamily && renderingMode === "rsc") {
    warnings.push('useRecommendations/<LikylyRecommendations> use client-side React state (hooks) - render them from a Client Component ("use client"), not directly inside a React Server Component.');
  }
  if (framework === "vue" || framework === "svelte") {
    warnings.push("No @likyly/vue or @likyly/svelte package exists yet - use @likyly/sdk's likyly.recommend()/likyly.events.* directly and wire the result into this framework's own reactivity (a computed/ref, a store, ...).");
  }
  if (requirements.fallback_strategy === null && requirements.strategy !== "auto" && requirements.strategy !== "popular") {
    warnings.push(`This placement's strategy ("${requirements.strategy}") has no fallback_strategy configured - it still always falls back to "popular" as a last resort, but consider setting one explicitly for a more relevant fallback than pure popularity.`);
  }

  return { packages, env, publicApi, requiredContext, requiredEvents, minimalExample, warnings };
}
