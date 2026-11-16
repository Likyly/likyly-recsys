// A store's catalog, connected by the wizard itself (Shopify, Magento): the developer types the
// credentials in the terminal (secrets masked) and they go straight to the LIKYLY API, which stores
// them encrypted. They never reach the AI assistant, a file or the theme.

import { API_URL, wizardUserAgent } from "./verify.mjs";

const ok = (text, hint) => ({ level: "ok", text, hint });
const warn = (text, hint) => ({ level: "warn", text, hint });
const fail = (text, hint) => ({ level: "fail", text, hint });
const info = (text, hint) => ({ level: "info", text, hint });

function client(apiKey, fetchImpl, agent) {
  return async (method, path, body) => {
    const res = await fetchImpl(`${API_URL}${path}`, {
      method,
      headers: { "X-API-Key": apiKey, "Content-Type": "application/json", "User-Agent": wizardUserAgent(agent) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(60000),
    });
    let data = null;
    try {
      data = await res.json();
    } catch {
      // empty or non-JSON body
    }
    return { status: res.status, ok: res.ok, data };
  };
}

const detail = (r) => (typeof r.data?.detail === "string" ? r.data.detail : `HTTP ${r.status}`);

/** The first error of a sync run, from its error_summary (a JSON list, or a plain message). */
function firstError(run) {
  try {
    const list = JSON.parse(run.error_summary ?? "[]");
    return Array.isArray(list) ? list[0]?.message : String(list);
  } catch {
    return run.error_summary;
  }
}

/**
 * Start the full sync and wait for its outcome (polling, up to ~3 minutes): the developer is
 * told what actually landed in LIKYLY, and why nothing did when it failed - never just
 * "started".
 */
async function firstSync(api, id, { wait, onProgress, timeoutMs = 180000 }) {
  const started = await api("POST", `/data-sources/${id}/sync`, { mode: "full" });
  if (!started.ok) return [fail(`The first sync could not start: ${detail(started)}`)];
  for (let elapsed = 0; elapsed < timeoutMs; elapsed += 3000) {
    await wait(3000);
    const runs = await api("GET", `/data-sources/${id}/syncs`);
    const run = runs.ok ? (runs.data ?? []).find((r) => r.id === started.data?.id) ?? runs.data?.[0] : null;
    if (!run) continue;
    if (run.status === "running") {
      onProgress?.(run.items_fetched ?? 0);
      continue;
    }
    const saved = run.items_upserted ?? 0;
    const rejected = run.items_failed ?? 0;
    const why = firstError(run);
    if (run.status === "failed" || (!saved && rejected)) {
      return [fail(`No product saved in LIKYLY${rejected ? ` (${rejected} rejected)` : ""}: ${why ?? "the sync failed"}`)];
    }
    const lines = [ok(`${saved} product(s) synced to LIKYLY`)];
    if (rejected) lines.push(warn(`${rejected} product(s) rejected: ${why}`));
    return lines;
  }
  return [warn("The first sync is still running: check it later in your LIKYLY console (Catalogue → Connecteurs)")];
}

/** The store said no: the developer can type the store and credentials again (`retry`). */
function refused(platform, test) {
  return [{ ...fail(`${platform} refused the connection: ${test.data?.message ?? detail(test)}`), retry: true }];
}

/**
 * Create (or, for the same store, update) the store's source, test it, start the first sync.
 * A source this call created and that fails its test is deleted again: nothing half-configured
 * is left behind (the free plan has room for one source). An existing source's credentials are
 * tested before they replace the stored ones. A refusal is marked `retry`.
 * - `platform`: "Shopify", "Magento" (in messages); `agent`: the User-Agent framework id;
 * - `label`: the store as the developer knows it; `matches(source)`: the existing source for it;
 * - `source`: the POST /data-sources body, without the credentials.
 */
export async function connectCatalog({ apiKey, platform, agent, label, matches, source: body, credentials, fetchImpl = fetch, wait = (ms) => new Promise((r) => setTimeout(r, ms)), onProgress }) {
  const api = client(apiKey, fetchImpl, agent);
  let created = false;
  let source;
  try {
    const list = await api("GET", "/data-sources");
    if (!list.ok) return [fail(`LIKYLY refused to list your sources: ${detail(list)}`)];
    source = (list.data ?? []).find(matches);
    if (source) {
      // Tried before they are saved: a typo never replaces credentials that work.
      const tried = await api("POST", `/data-sources/${source.id}/test`, { credentials });
      if (!tried.ok || !tried.data?.ok) return refused(platform, tried);
      const updated = await api("PATCH", `/data-sources/${source.id}`, { credentials });
      if (!updated.ok) return [fail(`LIKYLY could not update the ${label} source: ${detail(updated)}`)];
    } else {
      const made = await api("POST", "/data-sources", { ...body, credentials });
      if (!made.ok) return [fail(`LIKYLY could not create the ${platform} source: ${detail(made)}`)];
      source = made.data;
      created = true;
    }

    const test = await api("POST", `/data-sources/${source.id}/test`);
    if (!test.ok || !test.data?.ok) {
      if (created) await api("DELETE", `/data-sources/${source.id}`);
      return refused(platform, test);
    }

    const results = [ok(`Connected ${label} → catalog ${source.product_type}`, `(${test.data.message})`)];
    results.push(...(await firstSync(api, source.id, { wait, onProgress })));
    results.push(info("The credentials went straight to LIKYLY, encrypted: they are in no file and your assistant never sees them"));
    return results;
  } catch (error) {
    return [fail(`Could not reach the LIKYLY API (${API_URL}): ${error.message}`)];
  }
}

/**
 * The source of this platform already connected to LIKYLY, with its catalog's product count:
 * what the catalog step shows before asking for anything. null when there is none, or when
 * LIKYLY can't be asked (the step then simply asks for the store).
 */
export async function findConnectedSource({ apiKey, type, agent = type, matches = (s) => s.type === type, fetchImpl = fetch }) {
  const api = client(apiKey, fetchImpl, agent);
  try {
    const list = await api("GET", "/data-sources");
    const source = list.ok ? (list.data ?? []).find(matches) : null;
    if (!source) return null;
    const stats = await api("GET", `/data-sources/${source.id}/stats`);
    return { ...source, itemCount: stats.ok ? (stats.data?.item_count ?? null) : null };
  } catch {
    return null;
  }
}

/** "✓ my-shop.myshopify.com → catalog my-shop · 50 product(s)", as one line. */
export function describeSource(label, source) {
  const count = source.itemCount === null || source.itemCount === undefined ? "" : ` · ${source.itemCount} product(s)`;
  return `${label} → catalog ${source.product_type}${count}`;
}

/** Every item id the catalog has (GET /items, 1000 at a time). */
async function catalogItemIds(api, productType) {
  const ids = [];
  for (let offset = 0; offset < 100000; offset += 1000) {
    const page = await api("GET", `/items?data_product_type=${encodeURIComponent(productType)}&limit=1000&offset=${offset}`);
    if (!page.ok) break;
    const rows = page.data ?? [];
    ids.push(...rows.map((r) => String(r.item_id ?? r.id)));
    if (rows.length < 1000) break;
  }
  return ids;
}

/**
 * A store LIKYLY's servers can't reach (on the developer's computer): the wizard sends the products
 * itself to a push source (`webhook`), created for this store or found again (`matches`), whose
 * push secret is rotated each time (it is shown only once, and never kept). `items` are already
 * LIKYLY-shaped; `source` is the POST /data-sources body. Batches of 1000.
 */
export async function pushCatalog({ apiKey, platform, agent, label, matches, source: body, items, replace = false, fetchImpl = fetch, onProgress }) {
  const api = client(apiKey, fetchImpl, agent);
  try {
    const list = await api("GET", "/data-sources");
    if (!list.ok) return [fail(`LIKYLY refused to list your sources: ${detail(list)}`)];
    let source = (list.data ?? []).find(matches);
    let secret;
    if (source) {
      const rotated = await api("POST", `/data-sources/${source.id}/push-secret/rotate`);
      if (!rotated.ok) return [fail(`LIKYLY could not open the ${label} source: ${detail(rotated)}`)];
      secret = rotated.data?.push_secret;
    } else {
      const made = await api("POST", "/data-sources", body);
      if (!made.ok) return [fail(`LIKYLY could not create the ${platform} source: ${detail(made)}`)];
      source = made.data;
      secret = made.data?.push_secret;
    }
    if (!secret) return [fail("LIKYLY gave no push secret for the source")];

    const push = (payload) =>
      fetchImpl(`${API_URL}/data-sources/${source.id}/push`, {
        method: "POST",
        headers: { "X-Push-Secret": secret, "Content-Type": "application/json", "User-Agent": wizardUserAgent(agent) },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(120000),
      });

    // `replace`: the items are the whole catalog now (a new choice of categories): what it had
    // and no longer gets is removed first, so its room is free before the new products arrive.
    let removed = 0;
    if (replace) {
      const keep = new Set(items.map((i) => String(i.id)));
      const stale = (await catalogItemIds(api, source.product_type)).filter((id) => !keep.has(id));
      for (let i = 0; i < stale.length; i += 1000) {
        const res = await push({ items: [], deleted_ids: stale.slice(i, i + 1000) });
        if (!res.ok) return [fail(`LIKYLY refused to remove the products left out (HTTP ${res.status})`)];
        removed += Math.min(1000, stale.length - i);
      }
    }

    let upserted = 0;
    let failed = 0;
    for (let i = 0; i < items.length; i += 1000) {
      const res = await push({ items: items.slice(i, i + 1000) });
      let data = null;
      try {
        data = await res.json();
      } catch {
        // empty or non-JSON body
      }
      if (!res.ok) return [fail(`LIKYLY refused the products: ${typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`}`)];
      upserted += data?.upserted ?? 0;
      failed += data?.failed ?? 0;
      onProgress?.(Math.min(i + 1000, items.length));
    }

    let why = null;
    if (failed) {
      const runs = await api("GET", `/data-sources/${source.id}/syncs`);
      why = runs.ok ? firstError((runs.data ?? [])[0] ?? {}) : null;
    }
    if (!upserted && items.length) return [fail(`No product saved in LIKYLY${failed ? ` (${failed} rejected)` : ""}: ${why ?? "the push failed"}`)];
    const results = [ok(`Connected ${label} → catalog ${source.product_type}`, "(products sent from this computer)"), ok(`${upserted} product(s) sent to LIKYLY`)];
    if (removed) results.push(ok(`${removed} product(s) no longer chosen removed from LIKYLY`));
    if (failed) results.push(warn(`${failed} product(s) rejected: ${why}`));
    return results;
  } catch (error) {
    return [fail(`Could not reach the LIKYLY API (${API_URL}): ${error.message}`)];
  }
}
