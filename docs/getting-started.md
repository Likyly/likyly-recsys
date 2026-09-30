# Getting started with LIKYLY from Claude Code / Codex

LIKYLY doesn't ship its own chat. The coding agent you're already using **is** the
conversational interface: it inspects your project, calls the LIKYLY MCP server's tools, and
writes the integration into your codebase itself. This page is three journeys, in the order a
real integration goes:

1. [Connect my catalog](#1-connect-my-catalog) - data sources.
2. [Configure recommendations](#2-configure-recommendations) - placements.
3. [Integrate and validate](#3-integrate-and-validate) - SDK/React in your app, then prove it works.

Then one [zero-to-working walkthrough](#zero-to-working-recommendations) end to end.

No step here should require you to go read separate documentation by hand if the information
can be pulled through an MCP tool instead - that's the whole point of the tools below.

Want to try this yourself right now, without a real project on hand? `examples/demo-storefront`
is a small Next.js app with no LIKYLY code in it - a catalog page, a product page, no real auth
- built specifically to test all three journeys below end to end. Its README has three ready
to paste test prompts, one per journey. [`docs/SKILL.md`](SKILL.md) is the same guidance as
this page, written imperative/agent-first (Clerk's `SKILL.md` convention) - point an agent at
it directly instead of re-explaining the flow in your own words.

## Setup (once)

1. Create a developer key from your account (or ask an admin to): `POST
   /clients/me/developer-keys` with `scopes: ["sources:read", "sources:write",
   "placements:read", "placements:write"]` - see [`sdk/mcp/README.md`](../sdk/mcp/README.md).
   **Never** put this key in a browser bundle, a public file, a `NEXT_PUBLIC_*`/`VITE_PUBLIC_*`
   env var, or committed code - it's for your coding agent's local environment only.
2. Point your MCP client (`.mcp.json`, `claude_desktop_config.json`, ...) at
   `likyly-mcp-server` with `LIKYLY_API_KEY` set to that developer key.

## 1. Connect my catalog

> **Prompt:** "Connect my Shopify catalog to Likyly. Use product.id as the identifier, title
> and description for content, and pull price, categories, image and stock. Run a first sync."

The agent: `list_source_types` -> `create_data_source` -> `test_data_source` ->
`preview_data_source` -> `configure_field_mapping` -> `sync_data_source` -> `get_sync_status` /
`get_catalog_stats` to report back what happened. Full reference:
[`docs/data-sources.md`](data-sources.md) (source types, field mapping, sync engine).

Already have a catalog connected? Skip straight to step 2 with an existing `product_type`.

## 2. Configure recommendations

> **Prompt:** "Sur une fiche produit, affiche 4 recommandations. Pour un visiteur anonyme,
> base-toi sur le produit courant et sa session. Pour un utilisateur connecté, ajoute son
> historique. Exclue les produits indisponibles."

The agent: `create_placement` (strategy `auto` already does exactly the anonymous/identified
progression described - see [`docs/placements.md`](placements.md#strategy-resolution) - with
`filters.in_stock_only: true` for the last sentence) -> `preview_placement` against a couple of
real item ids to sanity-check the result -> `get_placement_requirements` to know exactly what
context the integration needs to send. Full reference: [`docs/placements.md`](placements.md).

## 3. Integrate and validate

> **Prompt:** "Intègre le placement pdp-related sur cette fiche produit React. Affiche 4
> cartes en respectant le design existant et ajoute tous les événements nécessaires à
> l'apprentissage de Likyly."

The agent:

1. Inspects the repo (framework, rendering mode, whether auth/user context is already
   available at this point in the code) - **LIKYLY never guesses this itself**.
2. `get_integration_recipe({ placement, framework, rendering_mode, has_auth_context })` -
   packages to install, env var names, the public API to call, this placement's real required
   context/events, a short illustrative snippet, and warnings. Not a whole file to paste - the
   agent adapts it to the actual `ProductPage` component it found.
3. Installs `@likyly/sdk` (+ `@likyly/react` for a React-family framework) and writes the
   integration: a `<LikylyProvider>` at the app root (or reuses the app's existing provider
   tree) with the **public** key, then `<LikylyRecommendations placement="pdp-related"
   itemId={product.id} userId={user?.id} />` (or `useRecommendations` if the project's design
   system needs full control over the cards - see [`sdk/react/README.md`](../sdk/react/README.md)).
   Impression/click tracking is automatic with either the component or the hook's `trackImpression`/
   `trackClick` helpers wired to the project's own card markup.
4. `get_tracking_requirements` for the rest of the funnel, then adds
   `likyly.events.productView`/`addToCart`/`purchase` at the actual point in the app where each
   happens (LIKYLY cannot detect these universally across every stack - see
   [`docs/events.md`](events.md)). For a Shopify/WooCommerce store, prefer the platform's own
   webhooks/order-completed hook over guessing at custom checkout code - see
   [`docs/data-sources.md`](data-sources.md#woocommerce-reusing-the-existing-connector-not-rebuilding-one).
5. `validate_integration({ slug: "pdp-related" })` - a real ✓/⚠/✗ checklist from LIKYLY's own
   data, with a concrete next step for anything not `ok`. Loops back to step 3/4 for whatever
   it flags, then validates again.

## Zero to working recommendations

The full chain, as one session with a coding agent, assuming nothing exists yet:

```
You: Connect my product catalog (a REST API at https://shop.example.com/api/products) to
     Likyly, mapping id/title/description/category/price/image/stock, and sync it.

Agent: [list_source_types -> create_data_source(type="rest_api", ...) -> test_data_source
        -> preview_data_source -> configure_field_mapping -> sync_data_source(mode="full")
        -> get_sync_status] "Connected - 214 items synced, 0 rejected."

You: Configure Likyly on my product pages. I want 4 alternatives. Personalize for logged-in
     users. For anonymous visitors, use the current product and the session. Never show the
     current product. Integrate it into my existing ProductPage component, matching my
     current styling. Add the required tracking. Then verify the integration.

Agent: [create_placement(slug="pdp-related", strategy="auto",
        signals={required:["current_item_id"], optional:["session_id","user_id"]})
        -> preview_placement -> get_placement_requirements
        -> get_integration_recipe(framework="react", ...)]
       "Created placement pdp-related. Installing @likyly/sdk and @likyly/react..."
       [edits ProductPage.tsx: <LikylyProvider>, <LikylyRecommendations placement="pdp-related"
        itemId={product.id} userId={user?.id} renderItem={...matches existing card...}/>]
       [get_tracking_requirements -> adds likyly.events.productView/addToCart/purchase at the
        page's existing view/cart/checkout handlers]
       [validate_integration]
       "Placement pdp-related:
        ✓ recommendations requested   ✓ current item supplied   ✓ anonymous session supplied
        ✓ impressions received        ✓ recommendation clicks received
        ⚠ product views not received yet - try viewing a product page once, then re-check
        ⚠ add_to_cart not received    ✗ purchases not received
        Everything wired for impressions/clicks; product_view/add_to_cart/purchase need a real
        visit through the app to confirm - I've added the calls, trigger each flow once and
        I'll re-validate."
```

This is not a scripted demo - it's exactly the sequence `tests/test_e2e_integration_journey.py`
exercises against the real API (data source -> sync -> placement -> recommend -> impression ->
click -> purchase -> validator), and the sequence `docs/data-sources.md`/`docs/placements.md`'s
own MCP smoke tests drove through a real, running MCP server.

## Reference

- [`docs/data-sources.md`](data-sources.md) - connectors, sync engine, field mapping.
- [`docs/placements.md`](placements.md) - the Placement object, strategy resolution, filters,
  the integration recipe & validator.
- [`docs/events.md`](events.md) - the event vocabulary, aliases, `identify`.
- [`sdk/js/README.md`](../sdk/js/README.md), [`sdk/react/README.md`](../sdk/react/README.md).
- [`sdk/mcp/README.md`](../sdk/mcp/README.md) - every MCP tool, which key each needs.
