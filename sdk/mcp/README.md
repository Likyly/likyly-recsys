# @likyly/mcp-server

MCP (Model Context Protocol) server for the LIKYLY API. Lets an MCP-compatible AI coding
assistant (Claude Code, Codex, Claude Desktop, Cursor...) call recommendations and record
events directly, connect and sync a catalog data source (Shopify, a REST API, a CSV, a
webhook, ...) end to end, configure a **Placement** - a named recommendation setup ("4 related
products on a PDP, session-based for anonymous visitors, add history once logged in, exclude
out-of-stock") - and then get a structured integration recipe for a real codebase
(`get_integration_recipe`) and verify the result from LIKYLY's own data instead of guessing
from the source (`validate_integration`). LIKYLY doesn't ship its own chat - the coding agent
calling these tools *is* the conversational interface; see
[`docs/getting-started.md`](../../docs/getting-started.md) for the full "zero to working
recommendations" walkthrough.

Two tiers of tools, gated by which key you give `LIKYLY_API_KEY` - the server itself doesn't
decide what a key can do, the API does:

- **Recommendations/events** (`get_recommendations`, `track_event`, ...) - safe with your
  **restricted public key** (account page's "Clé publique"). Safe in an untrusted assistant
  context, the same way client-side JS would be.
- **Admin tools** (`create_data_source`, `create_placement`, ...) - need your **secret key**,
  or better, a **developer key** (see below) - a third credential kind meant for exactly this:
  a coding agent managing your data sources and placements, without handing it full account
  access. **Never put a secret or developer key where browser-facing code could read it** -
  only in a trusted local environment (your own machine, Claude Code, Codex, CI) or
  server-side config.

If you only ever want recommendations/events, a public key is all you need - the admin tools
are simply unusable without a secret/developer key (a clear 401/403 in the tool result, not a
crash).

## Install

```bash
npm install -g @likyly/mcp-server
```

## Configure

**Recommendations/events only** (e.g. Claude Desktop's `claude_desktop_config.json`, or a
browser-facing use case) - a public key:

```json
{
  "mcpServers": {
    "likyly": {
      "command": "likyly-mcp-server",
      "env": {
        "LIKYLY_API_KEY": "your-restricted-public-key",
        "LIKYLY_BASE_URL": "https://api.likyly.com",
        "LIKYLY_PRODUCT_TYPE": "your-catalog-namespace"
      }
    }
  }
}
```

**Claude Code / Codex, with data-source admin tools** - first create a developer key (once,
from a server-side shell - never from a browser):

```bash
curl -X POST https://api.likyly.com/clients/me/developer-keys \
  -H "Authorization: Bearer <your Supabase session JWT>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Claude Code", "scopes": ["sources:read", "sources:write", "placements:read", "placements:write"]}'
# -> {"key": "...", ...} - copy `key`, it is shown once
```

Then point `.mcp.json` (or `claude_desktop_config.json`) at it:

```json
{
  "mcpServers": {
    "likyly": {
      "command": "likyly-mcp-server",
      "env": {
        "LIKYLY_API_KEY": "your-developer-key",
        "LIKYLY_BASE_URL": "https://api.likyly.com"
      }
    }
  }
}
```

`LIKYLY_PRODUCT_TYPE` is optional - set it if you only manage one catalog, so prompts don't need
to repeat it; every recommendation/event tool also accepts `product_type` per-call to override
it (the admin tools target a data source's own `product_type`, set at `create_data_source`).

## Tools

Recommendations/events - work with the public key:

| Tool | Maps to |
|---|---|
| `get_recommendations` | `POST /getRec` - LIKYLY picks the strategy; returns a `recommendation_id` |
| `get_popular_recommendations` | `GET /getRec/popular/{count}` |
| `get_cold_start_recommendations` | `GET /getRec/content/{item_id}/{count}` |
| `get_collaborative_recommendations` | `GET /getRec/collaborative/{user_id}/{count}` |
| `get_hybrid_recommendations` | `GET /getRec/hybrid/{user_id}/{item_id}/{count}` |
| `get_session_recommendations` | `GET /getRec/session` |
| `get_user_session_recommendations` | `GET /getRec/sessionForUser/{user_id}/{count}` |
| `track_event` | `POST /events/{event_type}` (impression, view, click, add_to_cart, remove_from_cart, purchase, ...) |

Admin - data source integrations - need the secret key or a developer key with `sources:read`/
`sources:write` (see [`docs/data-sources.md`](../../docs/data-sources.md) for the full
architecture, source types and config shapes):

| Tool | Maps to |
|---|---|
| `list_source_types` | `GET /data-sources/types` |
| `create_data_source` | `POST /data-sources` |
| `test_data_source` | `POST /data-sources/{id}/test` |
| `preview_data_source` | `POST /data-sources/{id}/preview` |
| `configure_field_mapping` | `PUT /data-sources/{id}/field-mapping` (or the `/dry-run` variant, with `dry_run: true`) |
| `sync_data_source` | `POST /data-sources/{id}/sync` |
| `get_sync_status` | `GET /data-sources/{id}/syncs` |
| `list_data_sources` | `GET /data-sources` |
| `get_catalog_stats` | `GET /data-sources/{id}/stats` |

Admin - placements (the recommended way to wire up recommendations - see
[`docs/placements.md`](../../docs/placements.md)) - need the secret key or a developer key
with `placements:read`/`placements:write`:

| Tool | Maps to |
|---|---|
| `list_placements` | `GET /placements` |
| `get_placement` | `GET /placements/{slug}` |
| `create_placement` | `POST /placements` |
| `update_placement` | `PATCH /placements/{slug}` |
| `delete_or_disable_placement` | `POST /placements/{slug}/deactivate` (default) or `DELETE /placements/{slug}` |
| `preview_placement` | `POST /placements/{slug}/preview` |
| `get_placement_requirements` | `GET /placements/{slug}/requirements` |

Admin - integration recipe & validator (writing and verifying the client-side integration -
see [`docs/getting-started.md`](../../docs/getting-started.md)) - same `placements:read`/
`placements:write` requirement:

| Tool | Maps to |
|---|---|
| `get_integration_recipe` | Composed from `GET /placements/{slug}/requirements` + `.../tracking-requirements` - not a new endpoint |
| `get_tracking_requirements` | `GET /placements/{slug}/tracking-requirements` |
| `validate_integration` | `GET /placements/{slug}/health`, rendered as a ✓/⚠/✗ checklist with next steps |
| `get_placement_health` | `GET /placements/{slug}/health` (structured data, no rendering) |
| `get_recent_integration_events` | `GET /placements/{slug}/recent-events` |

The runtime call, `POST /placements/{slug}/recommend`, is **not an MCP tool** - it's a plain
HTTP call the site's own code makes, safe with the public key, exactly like `POST /getRec`.

## Hosted service (connect any LLM by URL)

Besides the local stdio server above, the same tools can run as a hosted **Streamable HTTP**
service, so an MCP-capable LLM client connects with a URL instead of running a local process:

```bash
npm run build
PORT=3200 npm run start:http      # POST /mcp, GET /health
```

Each request carries the caller's own key (public, secret, or developer) - the service stores
no secrets and forwards it as-is to the LIKYLY API, so it can never do more than the key itself
allows. Only send a secret or developer key over this path from a trusted server-side caller,
same as anywhere else - never from browser-facing code:

```
POST https://mcp.likyly.com/mcp
Authorization: Bearer <public-secret-or-developer-key>
X-Likyly-Product-Type: <catalog-namespace>     # optional default catalog, recommendation/event tools only
```

Deploy on the droplet next to the API (Docker + Caddy):

```bash
docker build -t likyly-mcp sdk/mcp && docker run -d --restart=always -p 127.0.0.1:3200:3200 likyly-mcp
```

```caddyfile
mcp.likyly.com {
    reverse_proxy 127.0.0.1:3200
}
```

Env: `PORT` (default 3200), `LIKYLY_BASE_URL` (default `https://api.likyly.com`). Stateless mode:
no sessions, `GET /mcp` returns 405.

## Develop locally

```bash
npm install
npm run build
node dist/index.js   # reads LIKYLY_API_KEY / LIKYLY_BASE_URL / LIKYLY_PRODUCT_TYPE from env
```
