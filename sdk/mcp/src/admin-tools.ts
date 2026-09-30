import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { LikylyClient } from "./client.js";

// Admin tools: configuring and running data-source integrations (Shopify, a REST API, a
// CSV, a webhook, ...) - real, tenant-scoped configuration changes, not documentation. They
// need the secret key or a developer key carrying the matching scope (sources:read /
// sources:write - see /clients/me/developer-keys in the recsys API), never the restricted
// public key: whatever LIKYLY_API_KEY this server was started with is forwarded as-is, and
// the API itself is what enforces the scope - a caller using the wrong key gets a clear
// 401/403 back through the tool result, exactly like every other tool here.
//
// A typical flow: list_source_types -> create_data_source -> test_data_source ->
// preview_data_source -> configure_field_mapping -> sync_data_source -> get_sync_status /
// get_catalog_stats. Each tool below states which step(s) of that flow it is.
export function createLikylyAdminTools(server: McpServer, client: LikylyClient): void {
  function textResult(data: unknown) {
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  }

  const dataSourceId = z.number().int().describe("The data source's id, from create_data_source or list_data_sources.");

  server.tool(
    "list_source_types",
    "Step 1: every catalog source type LIKYLY can connect to (csv_upload, csv_url, json_url, rest_api, shopify, woocommerce, webhook) - each entry says whether it needs credentials and whether it supports incremental sync. Call this before create_data_source to pick the right `type` and know what `config`/`credentials` it expects (see docs/data-sources.md for each type's exact shape).",
    {},
    async () => textResult(await client.listSourceTypes()),
  );

  server.tool(
    "create_data_source",
    "Step 2: register a new catalog integration for this account. Doesn't fetch or write anything yet - it just saves the configuration. `product_type` is the catalog namespace it will write into (an existing one, or a new name to start a new catalog). `config` holds non-secret settings (e.g. {base_url, items_path} for rest_api, {shop_domain} for shopify); `credentials` holds secrets (e.g. {access_token} for shopify) and is encrypted at rest, never returned afterwards. Omit `sync_mode` to get the type's default. For a push-mode type (woocommerce, webhook) the response includes a one-time `push_secret` for the upstream system's webhook - save it, it can't be retrieved again. Call test_data_source next.",
    {
      name: z.string().min(1).max(200),
      type: z.string().describe("One of list_source_types' ids, e.g. 'shopify', 'rest_api', 'csv_url'."),
      product_type: z.string().min(1).max(64).regex(/^[a-zA-Z0-9_-]+$/).describe("The catalog namespace this source writes into."),
      config: z.record(z.unknown()).optional(),
      credentials: z.record(z.unknown()).optional().describe("Secrets - access tokens, API keys. Encrypted at rest."),
      field_mapping: z.record(z.unknown()).optional().describe("Can be set later via configure_field_mapping instead."),
      sync_mode: z.enum(["full", "incremental"]).optional(),
    },
    async (input) => textResult(await client.createDataSource(input)),
  );

  server.tool(
    "test_data_source",
    "Step 3: validates that this source's credentials/config actually reach it - never writes any catalog data. Always returns ok:true/false with a message (never throws for a bad connection), so you can report exactly why a connection failed.",
    { data_source_id: dataSourceId },
    async ({ data_source_id }) => textResult(await client.testDataSource(data_source_id)),
  );

  server.tool(
    "preview_data_source",
    "Step 4: fetches a small sample from the source (never writes to the catalog) and returns detected_fields plus a best-effort suggested_mapping - the starting point for configure_field_mapping. Call this before mapping fields, especially for a source type you haven't configured before.",
    { data_source_id: dataSourceId, limit: z.number().int().min(1).max(50).default(10) },
    async ({ data_source_id, limit }) => textResult(await client.previewDataSource(data_source_id, limit)),
  );

  server.tool(
    "configure_field_mapping",
    "Step 5: sets how this source's raw records map to a Likyly item. `mapping.external_id` and `mapping.title` are required; `description`, `category`, `price`, `image`, `url`, `stock`, `updated_at` are optional; `attributes` is itself {name: path} for anything else to keep as free-form properties. Each value is a field name or a dotted/bracket path into a nested record (e.g. 'variants[0].price', 'images[0].src') - start from preview_data_source's suggested_mapping. Pass dry_run:true to see the mapping's effect on a fresh sample WITHOUT saving it (use this to iterate); dry_run:false (the default) persists it for sync_data_source to use.",
    {
      data_source_id: dataSourceId,
      mapping: z.record(z.unknown()).describe("e.g. {external_id: 'id', title: 'name', price: 'variants[0].price', image: 'images[0].src', category: 'product_type', stock: 'variants[0].inventory_quantity'}"),
      dry_run: z.boolean().default(false).describe("true: preview the effect without saving. false: save it."),
    },
    async ({ data_source_id, mapping, dry_run }) =>
      textResult(dry_run ? await client.dryRunFieldMapping(data_source_id, mapping) : await client.saveFieldMapping(data_source_id, mapping)),
  );

  server.tool(
    "sync_data_source",
    "Step 6: runs a sync now (queued as a background job, so this returns almost immediately with the run's status - poll get_sync_status for completion). `mode` defaults to incremental (falls back to full automatically if the source doesn't support incremental); use mode:'full' explicitly for the first sync or to re-baseline. Not applicable to push-mode sources (woocommerce, webhook) - those sync automatically whenever the upstream system pushes; check get_sync_status/get_catalog_stats for their activity instead.",
    { data_source_id: dataSourceId, mode: z.enum(["full", "incremental"]).default("incremental") },
    async ({ data_source_id, mode }) => textResult(await client.syncDataSource(data_source_id, mode)),
  );

  server.tool(
    "get_sync_status",
    "Step 7: the most recent sync attempts for this source - status (running/success/partial/failed), items fetched/upserted/deleted/failed, and any error. Poll this right after sync_data_source until status is no longer 'running'. Also the right call to check on a push-mode source's activity.",
    { data_source_id: dataSourceId, limit: z.number().int().min(1).max(100).default(5) },
    async ({ data_source_id, limit }) => textResult(await client.listSyncs(data_source_id, limit)),
  );

  server.tool(
    "list_data_sources",
    "Every data source configured for this account, with its type, status, and last sync time - use this to check what already exists before creating a duplicate, or to find a data_source_id by name.",
    {},
    async () => textResult(await client.listDataSources()),
  );

  server.tool(
    "get_catalog_stats",
    "Item count and last-write time for a source's target catalog, plus its latest sync run - what you need to report back to the user after a sync (\"X items synced, Y rejected, next sync at ...\").",
    { data_source_id: dataSourceId },
    async ({ data_source_id }) => textResult(await client.getCatalogStats(data_source_id)),
  );
}
