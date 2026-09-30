# Placements: the recommended way to embed LIKYLY recommendations

**Placements are the abstraction to reach for first.** `POST /getRec` and the strategy-specific
`GET /getRec/*` endpoints remain fully supported as the **advanced/low-level API** - use them
when you want to call a specific engine yourself, per request, with no persistent
configuration. For everything else - "4 related products on a product page", "personalize the
homepage for logged-in visitors, cold-start otherwise", "cart cross-sell excluding what's
already in the cart" - configure a **Placement** once and call it by name from then on.

If you're working in Claude Code or Codex, skip to [Example prompts](#example-prompts).

## Why

Wiring up recommendations today means picking `content`, `session`, `collaborative`, `hybrid`
or `popular` by hand for every spot on a site, and re-deciding the same things each time: which
signals matter here, what's the fallback, do we exclude out-of-stock items. A `Placement` is a
named, persistent answer to all of that - "what to recommend, where, in what context, with what
constraints" - so a developer describes the *intent* once (by hand, or via a coding agent) and
the site calls a stable slug (`pdp-related`) from then on.

**This is an orchestration layer, not a new ML capability.** Every placement resolves, at
request time, to one of the existing engines in `application/api/recommender.py`
(`rec_content`, `rec_session`, `rec_collaborative`, `rec_hybrid`, `rec_popular`) - unchanged.
`strategy: "auto"` delegates straight to `recommend_auto`, the same signal-based selection
`POST /getRec` already used before Placements existed - not a new model, just the existing
choice given a name.

## The Placement object

| Field | Meaning |
|---|---|
| `slug` | Stable id, chosen by you (`pdp-related`, `homepage-personalized`, `cart-cross-sell`, ...) - what `/recommend` is called with. |
| `name` | Human label. |
| `context_type` | One of `product_page, listing_page, category_page, homepage, cart, account, content_page, custom` - descriptive/analytics tag, doesn't itself require any signal. |
| `product_type` | Catalog namespace to recommend from - omit it when the account has one catalog. |
| `limit` | Default result count (a `/recommend` call can override it). |
| `audience` | `all` \| `anonymous` \| `identified` - documents intent. |
| `strategy` | `auto` \| `content` \| `session` \| `collaborative` \| `hybrid` \| `popular`. |
| `signals` | `{required: [...], optional: [...]}` - see below. |
| `fallback_strategy` | Tried if `strategy` returns nothing. `popular` is always the final safety net regardless. |
| `filters` | Attribute-based inclusion rules - `category_in`, `category_not_in`, `in_stock_only`, `include_attributes`, `exclude_attributes`. |
| `business_rules` | Behavioral rules - `exclude_current_item` (default `true`), `exclude_cart_items`. |
| `tracking_configuration` | Reserved for a future per-placement tracking override - `{}` today. |
| `enabled`, `version` | Status; `version` increments on every update. |

### Context signals

Sent in `context` at `/recommend` / `/preview` time - every key optional unless a placement's
own `signals.required` names it:

`current_item_id, category_id, collection_id, user_id, anonymous_id, session_id,
cart_item_ids, locale, custom_context`.

- `current_item_id`, `user_id`, `session_id` feed the engines directly (content anchor,
  collaborative/hybrid user, session history).
- `anonymous_id` is used like `session_id` when `session_id` itself is omitted.
- `category_id` implicitly scopes results to that category when `filters.category_in` isn't
  set (see Filters below).
- `cart_item_ids` feeds `business_rules.exclude_cart_items`.
- `collection_id`, `locale`, `custom_context` are accepted and valid to declare in `signals`,
  but aren't consumed by any filter or engine yet - pass-through for now.

### Strategy resolution

- **`auto`**: calls `recommend_auto` exactly as `POST /getRec` does - `user_id` + `item_id` ->
  `hybrid`; `item_id` alone -> `content`; a session with tracked history (or an explicit
  session) -> `session`; a known user with a trained model -> `collaborative`; nothing
  usable -> `popular`. This covers most real placements, including the brief's canonical PDP
  example (anonymous: item + session -> `content`/`session`; identified: adds history).
- **An explicit strategy** (`content`/`session`/`collaborative`/`hybrid`/`popular`): runs that
  one engine only. If it returns nothing (e.g. `content` with no `current_item_id`,
  `collaborative` with no trained model yet), `fallback_strategy` is tried next; if that also
  comes back empty (or none was configured), `popular` always runs as the final safety net -
  a placement never just fails for "no results."

### Filters (`filters`, `application/api/placement_filters.py`)

Applied after ranking, to the already-presented items:

- `category_in` / `category_not_in` - by `properties.category`.
- `in_stock_only` - only filters an item that actually has a `stock` or `in_stock` property;
  never excludes one for lacking it.
- `include_attributes` / `exclude_attributes` - `[{"attribute": "brand", "values": [...]}]`.

### Business rules (`business_rules`)

- `exclude_current_item` (default `true`) - never recommends `context.current_item_id` back
  to itself.
- `exclude_cart_items` - never recommends anything in `context.cart_item_ids`.

Results can come back under `limit` after filtering - there's no re-fetch/top-up loop yet
(deliberately simple, per the brief - no merchandising engine).

## API

Admin (secret key or a developer key with `placements:read`/`placements:write`):

```
POST   /placements                       create
GET    /placements                       list
GET    /placements/{slug}                get
PATCH  /placements/{slug}                partial update (signals/filters/business_rules replace whole, not deep-merge)
POST   /placements/{slug}/deactivate     enabled=false (reversible)
DELETE /placements/{slug}                permanent
GET    /placements/{slug}/requirements   static: signals, strategy, fallback, context_type
POST   /placements/{slug}/preview        run against a sample context, never writes/attributes
```

Runtime (public key or secret key - same safety as `POST /getRec`):

```
POST   /placements/{slug}/recommend
{"context": {"current_item_id": "SKU-1", "session_id": "sess_abc"}, "limit": 4}
-> {"recommendation_id": "...", "placement": "pdp-related", "strategy_used": "content", "items": [...]}
```

`recommendation_id` attributes the `impression`/`click`/`add_to_cart`/`purchase` events that
follow, through the exact same mechanism `POST /getRec` uses (`track_event` needs no changes).
A disabled or unknown placement is a 404, indistinguishable from "never existed," including
across tenants.

## Client-side integration

The runtime call is plain HTTP (`POST /placements/{slug}/recommend`), but you don't have to
call it directly:

- **`@likyly/sdk`** (JS/TS): `likyly.recommend({ placement, context })` -
  `likyly.placements.recommend(...)`'s shorthand, and `likyly.events.recommendationImpression`/
  `recommendationClick`/`productView`/`identify` for tracking. See
  [`sdk/js/README.md`](../sdk/js/README.md).
- **`@likyly/react`**: `useRecommendations({ placement, context })` (headless - you render the
  markup) or `<LikylyRecommendations placement itemId userId />` (ready-to-use, auto-tracks
  impression/click via `IntersectionObserver`). A separate package from `@likyly/sdk` on
  purpose - see [`sdk/react/README.md`](../sdk/react/README.md) for why. Other frameworks use
  `@likyly/sdk` directly; there's no `@likyly/vue`/`@likyly/svelte` yet.
- **Anonymous sessions**: `@likyly/sdk`'s `LikylySession` is a first-party, cookieless
  anonymous id (`localStorage`, no third-party cookie) - `getAnonymousId()` for
  `context.anonymousId`/`sessionId`, and `identify(likyly, userId)` right after login to attach
  the session's past interactions to the now-known user (`POST /events/identify` - see
  [`docs/events.md`](events.md)).
- **Never in a browser bundle**: only the restricted public key belongs in client-side code.
  `@likyly/sdk`'s `Likyly` constructor refuses a secret/developer-shaped key (`sk_`/`lk_`
  prefix) when it detects it's running in a browser - see `sdk/js/README.md`'s "Which key?".

## Integration recipe & validator

Once a placement exists, a coding agent doesn't need to read this file to wire it up or check
its work - two MCP tools (backed by three read-only endpoints on this same router) do that:

- **`get_integration_recipe`** (composed from the two endpoints below - not a new one): tell it
  the framework you found in the project (react/nextjs/vue/svelte/vanilla-js/other) and it
  returns packages to install, env vars, the public API to call, this placement's actual
  required context/events, a short illustrative snippet (never a whole file to paste), and
  warnings (disabled placement, no auth context given, React Server Component misuse, ...).
- **`GET /placements/{slug}/tracking-requirements`** (`get_tracking_requirements`): which
  events this placement needs instrumented, and why.
- **`GET /placements/{slug}/health`** (`get_placement_health`, or `validate_integration` for a
  rendered ✓/⚠/✗ checklist with next steps): real counts from the last `window_hours` -
  recommend calls (and whether they carried the signals this placement requires), impressions,
  clicks, product views, add-to-carts, purchases. `add_to_cart`/`purchase` absent is `missing`
  only for `purchase` - `add_to_cart` absent is a `warning` (plenty of catalogs legitimately
  have none). `GET /placements/{slug}/recent-events` (`get_recent_integration_events`) gives
  the individual rows behind the counts, for when the aggregate alone doesn't explain a gap.

"Vérifie que mon intégration Likyly est correcte" should make Claude Code call
`validate_integration` and report what it actually says - not re-read its own previous edits
and guess.

## MCP tools

See [`sdk/mcp/README.md`](../sdk/mcp/README.md) for setup. `list_placements, get_placement,
create_placement, update_placement, delete_or_disable_placement, preview_placement,
get_placement_requirements, get_integration_recipe, get_tracking_requirements,
validate_integration, get_placement_health, get_recent_integration_events` -
`POST /placements/{slug}/recommend` is deliberately **not** an MCP tool: it's the runtime call
the site's own code makes directly (public key), not something the coding agent calls on the
site's behalf.

**No LLM call happens inside LIKYLY for any of this.** Turning "4 related products on a PDP,
session-based for anonymous visitors, add history once logged in" into the tool calls below is
exactly what the coding agent calling these tools already does - the tools are deterministic
primitives, not a second layer of natural-language understanding.

## Example prompts

> **"Configure Likyly on my product pages: 4 related items, based on the current product and
> the visitor's session; personalize further once we know who they are."**
>
> `create_placement(slug="pdp-related", context_type="product_page", limit=4, strategy="auto",
> signals={required: ["current_item_id"], optional: ["session_id", "user_id"]})` ->
> `preview_placement(slug="pdp-related", context={current_item_id: "<a real item id>"})` to
> check it -> `get_placement_requirements("pdp-related")` to know what to send -> the agent
> then wires the product-page template to `POST /placements/pdp-related/recommend` with the
> public key.

> **"On my homepage, personalize if the visitor is logged in, otherwise show cold-start
> discovery."**
>
> `create_placement(slug="homepage-personalized", context_type="homepage", strategy="auto",
> signals={required: [], optional: ["user_id", "session_id"]})` - `auto` already does exactly
> this (hybrid/collaborative once a known user has signal, otherwise popular/content) with no
> extra configuration.

> **"On the cart, recommend complementary content, excluding anything already in the cart."**
>
> `create_placement(slug="cart-cross-sell", context_type="cart", strategy="auto",
> signals={required: [], optional: ["cart_item_ids", "session_id", "user_id"]},
> business_rules={exclude_cart_items: true})`.

> **"Add a block of 6 recommended articles on article pages. An anonymous visitor gets content
> close to the current article; personalize further if we already know their reading
> history."**
>
> `create_placement(slug="article-related", context_type="content_page", limit=6,
> strategy="auto", signals={required: ["current_item_id"], optional: ["user_id",
> "session_id"]})` -> `preview_placement` with a couple of real article ids -> `get_placement_
> requirements` -> the agent can now wire the article template.

## Testing

`tests/test_placements.py` (end-to-end via the HTTP API) and `tests/test_placement_engine.py`
(the strategy/fallback resolution logic in isolation) cover: anonymous visitor, identified
user, session-only, current-item-only, no signal at all, `fallback_strategy` triggering,
filters (in-stock, category, cart exclusion), tenant isolation, scope enforcement, invalid and
disabled placements, and backward compatibility (`/getRec*` untouched).

`tests/test_placement_health.py` covers the validator endpoints (before/after activity,
irrelevant-signal checks omitted, tenant isolation, scope enforcement).
`tests/test_e2e_integration_journey.py` is the full brief's-Definition-of-Done proof: catalog
(a data source) -> sync -> placement -> recommend -> impression -> click -> purchase
attribution -> validator, driven only through the public HTTP surface a coding agent would
actually call. `sdk/react/test/controller.test.ts` covers the client-side fetch/tracking state
machine (`useRecommendations`/`<LikylyRecommendations>`'s shared logic) without needing a DOM.

## Environment variables & migrations

None. `PlacementModel` is a new table, created automatically by the existing
`Base.metadata.create_all` boot step - no SQL migration file needed (same reasoning as the
data-sources feature).
