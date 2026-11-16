// A Shopify store's catalog, connected by the wizard itself: the developer types the app's
// credentials in the terminal (the secret masked) and they go straight to the LIKYLY API, which
// stores them encrypted. They never reach the AI assistant, a file or the theme.

import { API_URL, wizardUserAgent } from "./verify.mjs";

const ok = (text, hint) => ({ level: "ok", text, hint });
const warn = (text, hint) => ({ level: "warn", text, hint });
const fail = (text, hint) => ({ level: "fail", text, hint });
const info = (text, hint) => ({ level: "info", text, hint });

/** "my-shop", "my-shop.myshopify.com", "https://my-shop.myshopify.com/admin" -> "my-shop.myshopify.com". */
export function normalizeShopDomain(input) {
  const v = String(input ?? "").trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  if (/^[a-z0-9][a-z0-9-]*$/.test(v)) return `${v}.myshopify.com`;
  if (/^[a-z0-9][a-z0-9-]*\.myshopify\.com$/.test(v)) return v;
  return null;
}

/** A shpat_ token (legacy custom app) or a Dev Dashboard app's client id + secret. */
export function shopifyCredentials(idOrToken, secret) {
  const first = String(idOrToken ?? "").trim();
  if (first.startsWith("shpat_")) return { access_token: first };
  const s = String(secret ?? "").trim();
  return first && s ? { client_id: first, client_secret: s } : null;
}

/** The catalog namespace: the shop's handle ("my-shop.myshopify.com" -> "my-shop"). */
export const catalogName = (shop) => shop.replace(/\.myshopify\.com$/, "").replace(/[^a-zA-Z0-9_-]/g, "-").slice(0, 64);

function client(apiKey, fetchImpl) {
  return async (method, path, body) => {
    const res = await fetchImpl(`${API_URL}${path}`, {
      method,
      headers: { "X-API-Key": apiKey, "Content-Type": "application/json", "User-Agent": wizardUserAgent("shopify") },
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

/** Shopify said no: the developer can type the store and credentials again (`retry`). */
function refused(test) {
  return [{ ...fail(`Shopify refused the connection: ${test.data?.message ?? detail(test)}`), retry: true }];
}

/**
 * Create (or, for the same shop, update) the `shopify` source, test it, start the first sync.
 * A source this call created and that fails its test is deleted again: nothing half-configured
 * is left behind (the free plan has room for one source). An existing source's credentials are
 * tested before they replace the stored ones. A refusal is marked `retry`.
 */
export async function connectShopifyCatalog({ apiKey, shop, credentials, fetchImpl = fetch, wait = (ms) => new Promise((r) => setTimeout(r, ms)), onProgress }) {
  const api = client(apiKey, fetchImpl);
  let created = false;
  let source;
  try {
    const list = await api("GET", "/data-sources");
    if (!list.ok) return [fail(`LIKYLY refused to list your sources: ${detail(list)}`)];
    source = (list.data ?? []).find((s) => s.type === "shopify" && s.config?.shop_domain === shop);
    if (source) {
      // Tried before they are saved: a typo never replaces credentials that work.
      const tried = await api("POST", `/data-sources/${source.id}/test`, { credentials });
      if (!tried.ok || !tried.data?.ok) return refused(tried);
      const updated = await api("PATCH", `/data-sources/${source.id}`, { credentials });
      if (!updated.ok) return [fail(`LIKYLY could not update the ${shop} source: ${detail(updated)}`)];
    } else {
      const made = await api("POST", "/data-sources", {
        name: `Shopify · ${shop}`,
        type: "shopify",
        product_type: catalogName(shop),
        config: { shop_domain: shop },
        credentials,
      });
      if (!made.ok) return [fail(`LIKYLY could not create the Shopify source: ${detail(made)}`)];
      source = made.data;
      created = true;
    }

    const test = await api("POST", `/data-sources/${source.id}/test`);
    if (!test.ok || !test.data?.ok) {
      if (created) await api("DELETE", `/data-sources/${source.id}`);
      return refused(test);
    }

    const results = [ok(`Connected ${shop} → catalog ${source.product_type}`, `(${test.data.message})`)];
    results.push(...(await firstSync(api, source.id, { wait, onProgress })));
    results.push(info("The credentials went straight to LIKYLY, encrypted: they are in no file and your assistant never sees them"));
    return results;
  } catch (error) {
    return [fail(`Could not reach the LIKYLY API (${API_URL}): ${error.message}`)];
  }
}
