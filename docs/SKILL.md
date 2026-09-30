---
name: likyly
description: Connect a catalog to LIKYLY, configure a recommendation placement, integrate it into a web app, and verify the integration - using the LIKYLY MCP server's tools. Use when a developer asks to add/configure/integrate/verify LIKYLY recommendations in their project.
---

# LIKYLY setup guide

LIKYLY has no chat of its own - **you are the interface**. Everything below is done through
the LIKYLY MCP server's tools, which make real, live changes to the developer's LIKYLY
account (they are not documentation lookups). If the MCP server isn't connected yet, tell the
developer to add it (see `sdk/mcp/README.md` in the LIKYLY repo, or ask them for their MCP
config) and stop - don't guess at endpoints or hand-write HTTP calls instead.

## Critical rules

- **Never** put a secret key or a developer key in code that ships to a browser: an env var
  read client-side, a `NEXT_PUBLIC_*`/`VITE_PUBLIC_*`-prefixed variable, a committed file, or
  anywhere `@likyly/react`'s `<LikylyProvider apiKey>` / `@likyly/sdk`'s browser-facing usage
  can see it. Only the **restricted public key** belongs there. If you only have a developer/
  secret key and need a public one for the browser, ask the developer to grab their public key
  from their LIKYLY account page - never mint or repurpose a developer key for this.
- **Never** guess at what framework, rendering mode, or auth system the project uses -
  inspect it (package.json, the router/framework in use, existing auth calls) and pass what
  you found into `get_integration_recipe`, don't assume.
- **Never** claim an integration works without calling `validate_integration` - report exactly
  what it says, not what you expect it to say.
- **Never** try to auto-track `product_view`, `add_to_cart` or `purchase` - there is no
  universal way to detect these across an arbitrary stack. Find the actual place in the
  project's own code where each happens and add the call there (see Phase 3).
- **Never** work around a plan limit. A `403` saying `plan limit reached` (a second catalog,
  a second data source, more products) or a `429` on a sync means the workspace's plan does
  not include that - the free plan is 1 catalog, 1 data source, 50 products and 1 sync per day.
  Do not retry, rename the catalog, split the catalog or push through another route: stop, tell
  the developer which limit was hit, and give them the upgrade link from the error message
  (there is no online payment yet - Pro is requested with a form in their LIKYLY console).
- Every strategy underneath a placement (`content`, `session`, `collaborative`, `hybrid`,
  `popular`, or `auto`) is an existing LIKYLY engine - don't reimplement any of this logic
  yourself, only configure it.

## Phase 1: Connect a catalog

Skip this phase if the developer already has a catalog/`product_type` connected - ask, or
check with `list_data_sources`.

1. `list_source_types` - see what LIKYLY can connect to and what each type's `config`/
   `credentials` shape looks like.
2. `create_data_source` with the type that matches what the developer described (a REST API,
   a CSV, Shopify, ...). This only saves configuration - nothing is fetched yet.
3. `test_data_source` - confirms LIKYLY can actually reach it. Fix the config/credentials and
   retry if it fails; don't move on with a broken connection.
4. `preview_data_source` - a small real sample plus a suggested field mapping.
5. `configure_field_mapping` with `dry_run: true` first to check the mapping's effect on the
   sample, then again with `dry_run: false` (or without the flag) to save it.
6. `sync_data_source` (`mode: "full"` for the first sync).
7. `get_sync_status` - poll until it's no longer `running`. Report the outcome plainly: items
   synced, items rejected (if any, say why), and the current status - this is what "verify the
   import" means, and the developer will ask for it explicitly if they don't get it
   unprompted.
8. `get_catalog_stats` if you want the resulting catalog's item count for the report above.

## Phase 2: Configure recommendations

1. Translate the developer's request into a `create_placement` call. `strategy: "auto"`
   covers most requests on its own (it already blends: identified user + current item ->
   hybrid; current item alone -> content; a session with history -> session; nothing usable ->
   popular) - reach for an explicit strategy only if the developer asked for one specific
   engine. Map their wording to fields:
   - "for anonymous visitors, use the current product and the session" -> `signals: {required:
     ["current_item_id"], optional: ["session_id", "anonymous_id"]}`, `strategy: "auto"`.
   - "personalize once they're logged in" -> add `"user_id"` to `signals.optional` (already
     covered by `auto`, nothing else to configure).
   - "never show the current product" -> `business_rules.exclude_current_item: true` (this is
     already the default - only set it explicitly if the developer wants to confirm it, or set
     it `false` if they explicitly want the opposite).
   - "exclude out-of-stock" -> `filters.in_stock_only: true`.
   - "exclude what's in the cart" -> `signals.optional` includes `"cart_item_ids"`,
     `business_rules.exclude_cart_items: true`.
   - "N items" -> `limit: N`.
   - Where on the site (product page, homepage, cart, ...) -> `context_type`.
2. `preview_placement` with a couple of real item ids (and a fake session/user id) from the
   project's own catalog - check `strategy_used` and `items` look right before moving on.
3. `get_placement_requirements` - exactly which context keys (`current_item_id`, `user_id`,
   `session_id`, ...) Phase 3's integration needs to supply.

## Phase 3: Integrate

1. Inspect the project: package manager, framework (React/Next.js/Vue/Svelte/vanilla/other),
   whether it's a Client or Server Component context at the integration point, and whether the
   current user's id is already available there (an existing auth hook/session).
2. `get_integration_recipe` with what you found (`placement`, `framework`, `rendering_mode`,
   `has_auth_context`). Use its `packages`/`env`/`publicApi`/`requiredContext`/`requiredEvents`/
   `warnings` - `minimalExample` is a short illustrative snippet, not a file to paste verbatim;
   adapt it to the component you're actually editing, reusing the project's existing markup/
   styling for each recommended item's card.
3. Install the packages the recipe named. For React/Next.js: `@likyly/sdk` + `@likyly/react`;
   otherwise `@likyly/sdk` alone.
4. Wire the public key in via whatever env var convention the project already uses for public/
   client-side config (see Critical rules) - `<LikylyProvider apiKey={...}>` once near the
   app's root for React/Next.js, or `new Likyly({ apiKey })` once for anything else.
5. Render the placement at the integration point:
   - React/Next.js: `<LikylyRecommendations placement="..." itemId={...} userId={...}
     sessionId={...} />` for a fast, auto-tracked (impression + click) integration, or
     `useRecommendations({...})` (headless) if the project needs full control of the markup -
     either way, pass `context.itemId`/`userId`/`sessionId` for whatever `get_placement_
     requirements` named. Use `LikylySession` (from `@likyly/sdk`, exposed as
     `useLikyly().session` under `@likyly/react`) for an anonymous session id if the project
     has none of its own yet.
   - Anything else: `likyly.recommend({ placement, context })`, render the items yourself,
     call `likyly.events.recommendationImpression`/`recommendationClick` for the events
     `<LikylyRecommendations>` would have handled automatically.
6. `get_tracking_requirements` for the rest of the funnel. For each event that isn't already
   automatic, find the real place it happens in the project's own code and add the call there:
   `likyly.events.productView` on the product page's own render/mount, `.addToCart` at the
   project's actual add-to-cart handler, `.purchase` (with an `eventId`) at its actual
   checkout-complete point. For a native Shopify/WooCommerce store, prefer the platform's own
   order-completed webhook/event over guessing at custom checkout code.
7. If the project has real login, call `likyly.events.identify` (or `LikylySession.identify`)
   right after it succeeds, so the visitor's pre-login session history gets attached to them.

## Phase 4: Verify

1. `validate_integration` for the placement you just wired up.
2. Report its checklist plainly (✓/⚠/✗ per check) - don't paraphrase it into "looks good."
3. For anything not `ok`: `get_recent_integration_events` if you need to see the actual rows
   behind a count, then fix the real cause (a missing call, a wrong `data_product_type`, a
   context key never sent) and validate again. A freshly-added event may need one real
   interaction (view the page, click a card, ...) before it shows up - say so rather than
   treating it as broken on the first check.

## Example: one prompt, the whole thing

> "Configure LIKYLY on my product pages. I want 4 alternatives. Personalize for logged-in
> users. For anonymous visitors, use the current product and the session. Never show the
> current product. Integrate the result into my existing ProductPage component, using my
> current styling. Add the required tracking. Then verify the integration."

Run Phases 2-4 in order for this (Phase 1 only if no catalog is connected yet) - nothing here
needs the developer to go read LIKYLY's docs by hand if you can get the same information
through the tools above.
