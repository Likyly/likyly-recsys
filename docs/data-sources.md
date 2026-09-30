# Data sources: connecting a catalog to LIKYLY from a coding agent

This is the reference for the data-source integration feature: a `DataSource` abstraction, a
`Connector` contract, a reusable sync engine, and the MCP admin tools that let Claude Code,
Codex or another coding agent connect and sync a customer's catalog end to end - without
either duplicating the existing catalog API or requiring a human to click through a dashboard.

If you just want to *use* it from Claude Code, skip to [Example prompts](#example-prompts).

## Architecture

```
 MCP admin tools (sdk/mcp)
        |  (secret key or a developer key with sources:* scope)
        v
 /data-sources* routes (application/api/data_sources.py)
        |
        v
 data_source_service.py  --------->  connectors/*.py (Connector contract)
        |                                   |
        v                                   v
 sync_engine.py  <-----------------  NormalizedItem (external_id, title, description,
        |                             categories, attributes, price, image, url, stock,
        |                             updated_at, raw_metadata)
        v
 ingestion.py (upsert_item / upsert_items_batch / delete_items)   <- unchanged, reused as-is
        v
 products table (the same catalog PUT /items already writes to)
```

Nothing about the existing catalog write path changed. A data source is a *configuration* that
tells the sync engine which connector to run, on which schedule/trigger, into which existing
catalog namespace (`product_type`) - the actual write is the same `upsert_item` call a manual
`PUT /items/{id}` would make.

### DataSource

A `DataSource` (`application/utils/db.py`'s `DataSourceModel`) has:

| Field | Meaning |
|---|---|
| `id`, `client_id`, `name` | Identity - tenant-scoped like everything else in this API. |
| `type` | One of the registry's ids - see [Source types](#source-types) below. |
| `product_type` | The existing catalog namespace this source writes into. |
| `config` | Non-secret settings (base URL, pagination shape, ...) - shape depends on `type`. |
| `credentials_encrypted` | Secrets (tokens, API keys), encrypted at rest - see [Credentials](#credentials--developer-keys). Never returned by any endpoint. |
| `field_mapping` | How raw source fields map to a Likyly item - see [Field mapping](#field-mapping). |
| `sync_mode` | `full`, `incremental`, or `push` (fixed for push-only types). |
| `schedule` | Reserved for a future scheduler - every sync today is triggered explicitly (`sync_data_source` / `POST /data-sources/{id}/sync`), there is no cron yet. |
| `status`, `last_sync_at`, `last_success_at`, `last_error`, `cursor` | Live state, updated by every sync attempt. |

### Connector contract

Every source type implements `application/utils/connectors/base.py`'s `Connector`:

```python
test_connection() -> ConnectionTestResult          # never writes; ok=False with a message on failure, never raises
preview(limit) -> PreviewResult                     # a small sample + detected_fields + a suggested_mapping
full_sync(checkpoint) -> Iterator[SyncPage]          # the authoritative, complete listing
incremental_sync(checkpoint) -> Iterator[SyncPage]   # only what changed since checkpoint - NotSupported if the type can't
normalize_item(raw, field_mapping) -> NormalizedItem
get_checkpoint() -> dict                             # only overridden if a page's own checkpoint isn't enough
```

`SyncPage` carries a checkpoint *per page*, not just at the end - the sync engine persists it
after every page it processes. Adding Akeneo, Contentful or Strapi later is exactly one new
module implementing this contract plus one new entry in `connectors/registry.py` - nothing in
the sync engine, the API routes, or the MCP tools needs to change.

### Source types

| `type` | `config` (non-secret) | `credentials` | Sync modes |
|---|---|---|---|
| `csv_upload` | `{}` (set by `POST /data-sources/{id}/upload`) | none | full only |
| `csv_url` | `{"url", "format": "csv"}` | `{"header": "...", "value": "..."}` (optional) | full only |
| `json_url` | `{"url", "format": "json", "items_path": "..."}` | same as `csv_url` | full only |
| `rest_api` | `{"base_url", "items_path", "pagination": {...}, "auth": {...}, "updated_since_param"}` | `{"token"}` or `{"username","password"}` | full + incremental |
| `shopify` | `{"shop_domain", "api_version", "page_size"}` | `{"access_token"}` | full + incremental |
| `woocommerce` | `{"site_url"}` (optional) | none | **push only** - see below |
| `webhook` | `{"sample": [...]}` (optional, for preview only) | none | **push only** |

`rest_api.pagination.type` is one of `page` (`page`/`per_page` params), `cursor` (a
`next_cursor`-style field in the response), or `none` (single page). `rest_api.auth.type` is
`bearer`, `api_key` (with a configurable header name), `basic`, or `none`.

### WooCommerce: reusing the existing connector, not rebuilding one

`likyly-wordpress/likyly-connector/likyly-connector.php` already pushes a WordPress site's
catalog (including WooCommerce's `product` post type, once selected in the plugin's settings)
via `PUT /items` on `save_post`, and purchase events via `POST /events/purchase` on
`woocommerce_order_status_completed`. That plugin is **unmodified** by this feature - the
`woocommerce` data source type is a thin, read-only adapter: `test_connection`/`preview`
optionally hit the site's public `wp-json/` root and WooCommerce's public Store API for
field-mapping visibility, and its "last sync" status is simply the most recent write already
recorded in the target catalog (`db.get_last_item_write_at`) - i.e. it *observes* the plugin's
own activity rather than pulling separately. `sync_data_source` on a `woocommerce` (or
`webhook`) source is a no-op by design (422: "sources sync automatically via push") - there is
nothing for it to trigger.

### Sync engine

`application/utils/sync_engine.py`'s `SyncEngine.run(mode)`:

1. Builds the connector from `type` + decrypted `credentials` + `config`.
2. For each raw record from `full_sync`/`incremental_sync`: normalizes it, computes a content
   hash of the normalized fields, and **skips the upsert entirely if the hash is unchanged**
   since this source last produced that `external_id` (tracked in a dedicated
   `data_source_items` table - separate from the hot-path `id_map`/`products` tables).
3. A record that fails to normalize or upsert is counted and skipped, not fatal to the run
   (partial failures) - reported in the sync run's `error_summary`.
4. Transient network errors retry with `tenacity` (bounded backoff) at the connector's own
   request level.
5. The connector's checkpoint is persisted after every page, not just at the end.
6. **Only a full sync** diffs the ids it just saw against what this source produced last time
   and deletes ones no longer present (delete/tombstone) - an incremental sync's listing isn't
   a complete snapshot, so it never triggers a deletion, and a full sync always restarts from
   page 1 (never resumes a stale cursor across separate calls) precisely so that diff stays
   trustworthy.
7. Writes one `data_source_sync_runs` row per attempt (`items_fetched/upserted/deleted/failed`,
   `status`) - what `get_sync_status`/`GET /data-sources/{id}/syncs` reads.

Triggering a sync (`POST /data-sources/{id}/sync`) queues this as a background task and
returns immediately with the run in `status: "running"` - the same pattern the existing
`GET /generateModel` background training job already uses.

### Credentials & developer keys

A third credential kind, alongside the existing secret/public API keys:

| Key | Scope | Where it's safe |
|---|---|---|
| Public key | Recommendations + event tracking only | Browser-side JS |
| Secret key | Everything, unchanged | Server-side only |
| **Developer key** | Whatever explicit `scopes` it was minted with (this release enforces `sources:read`/`sources:write`; `catalog:read`, `catalog:write`, `placements:read`, `placements:write`, `integrations:read`, `events:read` are reserved for future resources) | A trusted local dev environment (your machine, Claude Code, Codex, CI) - **never** browser-facing code |

Minted via the same self-service pattern as key rotation:

```
POST   /clients/me/developer-keys            {"name": "...", "scopes": ["sources:read", "sources:write"]}  -> {"key": "...", ...}  (shown once)
GET    /clients/me/developer-keys             -> [...] (never includes the raw key)
DELETE /clients/me/developer-keys/{id}         -> revokes immediately, no replacement
```

Data-source credentials themselves (a Shopify access token, a REST API key, ...) are encrypted
at rest with `cryptography.fernet.Fernet`, keyed by `LIKYLY_DATA_SOURCE_ENCRYPTION_KEY` - see
[Environment variables](#environment-variables--migrations). They are never returned by any
endpoint once set (`GET`/`list` responses only say `has_credentials: true/false`).

### Field mapping

A mapping is `{likyly_field: "source_path"}`; a path is a field name or a dotted/bracket path
into a nested record (`variants[0].price`, `images[0].src`). Recognized keys: `external_id`
and `title` (required), `description`, `category`, `price`, `image`, `url`, `stock`,
`updated_at` (all optional), and `attributes` (itself `{name: path}` for anything else to keep
as free-form item properties).

Flow: `preview_data_source` (sample + `suggested_mapping`) -> `configure_field_mapping` with
`dry_run: true` (iterate without saving) -> `configure_field_mapping` with `dry_run: false`
(persist) -> `sync_data_source`.

### Push ingress

`woocommerce`/`webhook` sources don't get pulled - an upstream system calls
`POST /data-sources/{id}/push` directly, authenticated by that source's own push secret
(`X-Push-Secret` header, issued once at creation or via `POST /data-sources/{id}/push-secret/rotate`) rather than a tenant API key:

```json
{"items": [{"id": "p1", "name": "..."}], "deleted_ids": ["p9"]}
```

Each pushed item goes through the same field-mapping + upsert path as a pull sync; `deleted_ids`
maps to the same `delete_items` call a full sync's tombstone diff uses.

## MCP admin tools

Added to `sdk/mcp` alongside the existing recommendation/event tools (`get_recommendations`,
`track_event`, ...), which are unchanged. See [`sdk/mcp/README.md`](../sdk/mcp/README.md) for
setup. Every tool below needs `LIKYLY_API_KEY` to be a secret key or a developer key with
`sources:read`/`sources:write` - a public key gets a clear 401/403 back through the tool
result, not a crash.

| Tool | What it does | API route |
|---|---|---|
| `list_source_types` | Every connector type available, with its config/credential requirements | `GET /data-sources/types` |
| `create_data_source` | Registers a new integration (doesn't fetch/write anything yet) | `POST /data-sources` |
| `test_data_source` | Validates the connection without writing catalog data | `POST /data-sources/{id}/test` |
| `preview_data_source` | Small sample + detected fields + a suggested mapping | `POST /data-sources/{id}/preview` |
| `configure_field_mapping` | Saves a mapping, or (`dry_run: true`) previews its effect without saving | `PUT` / `POST .../field-mapping/dry-run` |
| `sync_data_source` | Runs a sync now (background job) | `POST /data-sources/{id}/sync` |
| `get_sync_status` | Recent sync runs - status, counts, errors | `GET /data-sources/{id}/syncs` |
| `list_data_sources` | Every source configured for this account | `GET /data-sources` |
| `get_catalog_stats` | Item count, last write, latest sync for a source's catalog | `GET /data-sources/{id}/stats` |

After a sync, an agent has everything the brief's UX asks for from these tools alone: source
created (`create_data_source`'s response), connection validated (`test_data_source`), mapping
saved (`configure_field_mapping`), items synced/rejected and last-sync time
(`get_sync_status`/`get_catalog_stats`) - `schedule`/`sync_mode` say whether a next sync is
automatic (not yet - see `schedule`'s note above) or must be triggered again.

## Example prompts

These work today, end to end, with a developer key configured in `LIKYLY_API_KEY`:

> **"Connect my Shopify catalog to Likyly. Use product.id as the identifier, the title and
> description for semantic content, and pull price, categories, image and stock. Run a first
> import."**
>
> The agent: `create_data_source(type="shopify", config={shop_domain, page_size}, credentials={access_token})`
> → `test_data_source` → `preview_data_source` → `configure_field_mapping({external_id: "id",
> title: "title", description: "body_html", category: "product_type", price:
> "variants[0].price", image: "image.src", stock: "variants[0].inventory_quantity"})` →
> `sync_data_source(mode="full")` → `get_sync_status` / `get_catalog_stats` to report back.

> **"This project has an API at https://shop.example.com/api/products that returns
> {\"products\": [...]} with page/per_page pagination. Configure it as a Likyly source,
> mapping id, title, description, category, price, image and stock, then sync it."**
>
> `create_data_source(type="rest_api", config={base_url, items_path: "products",
> pagination: {type: "page", page_size: 100}})` → `preview_data_source` →
> `configure_field_mapping(...)` → `sync_data_source(mode="full")`.

> **"We publish our catalog as a CSV at https://cdn.example.com/catalog.csv. Connect it to
> Likyly and keep it in sync."**
>
> `create_data_source(type="csv_url", config={url, format: "csv"})` → `preview_data_source` →
> `configure_field_mapping(...)` → `sync_data_source(mode="full")` (CSV/URL sources are
> full-sync only - re-run `sync_data_source` whenever the file changes; unchanged rows are
> skipped automatically via the content-hash check).

> **"Check whether the Shopify source I set up last week is still syncing correctly."**
>
> `list_data_sources` (find it by name) → `get_sync_status` → `get_catalog_stats`.

## Testing

`pytest` covers (see `tests/test_developer_keys.py`, `tests/test_data_sources.py`,
`tests/test_connectors.py`, `tests/test_sync_engine.py`): tenant isolation on every route,
scope enforcement (public key rejected, a scope-limited developer key rejected on write
routes, secret key always allowed, revoked keys rejected), idempotency (an unchanged record
isn't re-upserted), field mapping (dry-run doesn't persist, `suggest_mapping` picks sane
defaults), pagination (page and cursor styles), incremental sync (checkpoint persists across
separate calls), deletion/tombstone (full sync only), invalid credentials (a clear error,
`status: "error"`, no partial corruption), partial failures (one bad record doesn't abort the
run), and retry/checkpoint (a transient failure retries and succeeds).

No integration here is claimed to work beyond what's actually exercised: the CSV/URL and
generic REST connectors are tested against a monkeypatched HTTP layer (no real network in
CI); Shopify's connector shares the same pagination/retry code path as the REST connector's
tests but has not been run against a real Shopify store as part of this change - test it
against a real store's Admin API before relying on it in production.

## Environment variables & migrations

| Variable | Required for | Notes |
|---|---|---|
| `LIKYLY_DATA_SOURCE_ENCRYPTION_KEY` | Creating/updating any data source with `credentials` | A `cryptography.fernet.Fernet` key - generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and set it once per environment. **Never rotate it by just changing the value** - every already-stored credential becomes undecryptable. Missing entirely: sources with no credentials (CSV, generic webhook, WooCommerce) still work fine; creating a `shopify`/credentialed `rest_api` source fails closed with a clear error instead of storing plaintext. |

No SQL migration file was added: every new table (`developer_keys`, `data_sources`,
`data_source_sync_runs`, `data_source_items`) is created automatically by the existing
`Base.metadata.create_all` boot step (`db.init_db`) the first time the API starts against a
database that doesn't have them yet - the project's migration runner
(`application/utils/migrate.py`) is only needed for altering an *existing* table, which
nothing here does.

Gateway: add `/data-sources/*` to the existing 5 req/s "account" rate-limit tier in
`gateway/bootstrap-route.sh` (same tier as `/admin/*`, `/clients/*`) - see that file's
`recsys-api-account` route. This is a manual re-run against the droplet's APISIX admin API,
not applied automatically by a code change.

## Backward compatibility

Nothing existing changed behavior: `/items`, `/users`, `/events*`, the secret/public key auth
dependencies, the MCP recommendation/event tools, and every SDK are untouched. The full
existing test suite passes unmodified alongside the new tests above.
