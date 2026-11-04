---
name: likyly
description: Connect a catalog to LIKYLY, configure a recommendation placement, integrate it into a web app, and verify the integration - using the LIKYLY MCP server's tools. Use when a developer asks to add/configure/integrate/verify LIKYLY recommendations in their project.
---

# LIKYLY setup guide

LIKYLY has no chat of its own - **you are the interface**. Everything below is done through
the LIKYLY MCP server's tools, which make real, live changes to the developer's LIKYLY
account (they are not documentation lookups). If the LIKYLY tools aren't available, answer
with the "Not connected" message below and stop - don't guess at endpoints or hand-write HTTP
calls instead.

### Not connected

Check first that the `likyly` tools really are missing right now (they may have connected since
the developer's last message): never say both "not connected" and "connected". Then answer with
exactly this, nothing more:

```
**LIKYLY · ❌ Not connected** - Claude Code can't reach the LIKYLY MCP server.

**Your account**
- Account: <email>
- Workspace: <name>

**What to do**
1. Type `/mcp`, choose `likyly`, then **Reconnect**.
2. When it shows ✔ connected, send your request again.
```

- Account and workspace come from the comment lines of `.likyly/credentials.env`; leave the
  block out if the file is missing - then step 1 is "Run `npx @likyly/wizard mcp add` in a
  terminal, then restart Claude Code in this folder".
- Don't diagnose: the server reads its key from `.likyly/credentials.env` by itself, so never ask
  to export or `source` anything. If Reconnect fails, the developer pastes the error shown by
  `/mcp` and only then you explain.

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

**An existing source that is out of date** (old URL, old catalog, ids that no longer match the
code) is not a reason to stop: fix it with `update_data_source` (new `config.base_url`, new
`product_type`...). This is not working around the one-source limit - it is the same source.
Only `delete_data_source` after the developer agrees, since it is irreversible.

**A catalog LIKYLY cannot reach** - the site runs on `localhost`, `127.0.0.1`, a `.local` or
private address, behind a VPN, or the products live only in the codebase - cannot use a pull
type (`rest_api`, `json_url`, `csv_url`): LIKYLY's servers would try to call their own
localhost. Use a push source instead, no tunnel needed:
1. `create_data_source` with `type: "webhook"` (or `update_data_source` on the existing one)
   and keep the one-time `push_secret` from the response (`rotate_push_secret` if it is lost).
2. Read the products from the project itself (its API route handler, JSON files, seed, database
   module), map them to items with the same ids the code uses on product pages.
3. `push_items` in batches of up to 1000, then `get_catalog_stats` to confirm the count.
Tell the developer that a production deployment should switch to a pull source on the public
URL, or call the push endpoint from their own code when products change.

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

**Always ask on which branch, before editing any file.** Run `git branch --show-current` and
`git status --short`, then ask in one question with numbered choices, and wait for the answer -
every time, even when the developer seems to be on the right branch already:

- On `likyly/setup` (the wizard's branch): "1. Keep working on likyly/setup - recommended ·
  2. Cancel".
- On any other branch `<b>`: "1. On a new branch likyly/setup (from `<b>`, which stays as is) -
  recommended · 2. Directly on `<b>` · 3. Cancel". Choice 1: `git switch likyly/setup`, or
  `git switch -c likyly/setup` when it does not exist.
- Not a git repository: "I'll change files directly (no git to review them). 1. Go · 2. Cancel".

Then say in one line where you work. Their uncommitted work comes along untouched: never stash, reset, checkout or restore a file you did not write
yourself. Never commit or push unless the developer asks; when they ask for a pull request,
commit only the files you changed (not their pending work), push the branch and open it with
`gh pr create`.

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

**Cached answers.** LIKYLY keeps an anonymous visitor's answer for a product for an hour (and
the site's SDK keeps it in the browser): a page can show the previous recommendations right after
a change. A catalog or placement change empties LIKYLY's cache on its own; for anything else
(stock handled outside LIKYLY, a deploy) or when the developer wants to see fresh results now,
call `purge_recommendation_cache` (the developer can also run `npx @likyly/wizard cache clear
[placement]` or `/likyly-clear-cache` in Claude Code), and tell them the browser keeps its own
copy per tab: open the page in a new tab to see the fresh recommendations. Never purge on every check: it is not needed to validate an integration.

## Final report

End every LIKYLY task - finished or stopped - with this report and nothing after it: a table
the developer reads in five seconds, links to check for themselves, and the single next
action. Write it in the developer's language. **20 lines at most** outside the table. No
narration of what you did, no tool names, no scopes, no explanations of how LIKYLY works,
no list of optional clean-ups.

```
**LIKYLY · <✅ Live | ⚠️ Not showing yet | ❌ Blocked>** - <reason in a few words, only if not ✅>

| | | |
|---|---|---|
| ✅ | Catalog | <catalog> · <N> products |
| ✅ | Recommendations | "<placement title>" · <route> · <N> items |
| ✅ | Tracking | <events, e.g. views, add to cart> |
| ❌ | Display | <the one thing blocking, ≤ 8 words> |

**Review** (nothing committed · branch `<branch>`)
- <N> files: `<path>` (new), `<path>` (modified), ...
- VS Code: Source Control (Ctrl/Cmd+Shift+G) shows each change side by side · or `git diff`
- Keep it: ask me to commit and open a pull request · Drop it: `git switch <base> && git branch -D likyly/setup`

**Check**
- Your page: <exact local URL>
- In LIKYLY: https://www.likyly.com/account/recommendations (status of each placement)
- Your catalog: https://www.likyly.com/account/catalogues/<catalog>/parcourir

**Your account**
- Account: <email>
- Workspace: <name>

**Commands**
| Command | What it does |
|---|---|
| `/likyly-help` | Lists these commands and what you can ask in plain words |
| `/likyly-clear-cache [placement]` | Shows fresh recommendations right away (otherwise they refresh within the hour) |
| `/likyly-uninstall` | Removes LIKYLY from this project: shows what it will remove, asks on which branch |

**Next:** <one action, in one short sentence>
```

- One row per area, ✅ ⚠️ ❌ only, the detail column under ten words. Add a row only for
  something that matters to the result (a missing purchase event when the shop has no
  checkout is not a row: leave it out).
- ✅ Live only when `validate_integration` shows recommendations requested and returned with
  no ✗. A server error from LIKYLY (5xx) is ❌ Display.
- **Next** says what the developer does, in plain words, never an id or a code. For a LIKYLY
  server error: "Nothing to change in your code: LIKYLY's API fails on this placement. Reload the
  page once it is fixed, then ask me to check again." Give the request id only if the developer
  asks how to report it.
- **Review**: list every file you created or changed (from `git status --short`, minus what was
  already modified before you started), new ones marked "new". Leave out the "Drop it" hint when
  you worked on the developer's own branch. Not a git repository: list the files and skip the rest.
- **Your account**: read the account and workspace from the comment lines of
  `.likyly/credentials.env` (`# Account:`, `# Workspace:`); leave a line out if it is not there.
  The developer signs in to likyly.com with this account to see the catalog.
- **Commands**: one row per command that exists in the project (`.claude/commands/likyly-*.md`),
  each with what it does in plain words. Outside Claude Code, use the terminal forms instead:
  `npx @likyly/wizard cache clear [placement]` and `npx @likyly/wizard uninstall`.
- **Next** is the single most useful action, in one sentence, with no explanation of causes. If
  you made a mistake along the way (a file changed by accident), it goes there instead.
- The developer can ask for details: then, and only then, explain.

## Example: one prompt, the whole thing

> "Configure LIKYLY on my product pages. I want 4 alternatives. Personalize for logged-in
> users. For anonymous visitors, use the current product and the session. Never show the
> current product. Integrate the result into my existing ProductPage component, using my
> current styling. Add the required tracking. Then verify the integration."

Run Phases 2-4 in order for this (Phase 1 only if no catalog is connected yet) - nothing here
needs the developer to go read LIKYLY's docs by hand if you can get the same information
through the tools above.
