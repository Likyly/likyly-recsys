// `npx @likyly/wizard pages`: where to see LIKYLY on the site - one example URL per kind of page
// that has a recommendation block (a product, a category, the home page, the cart...), never the
// whole catalog. Locally (the preview / dev server) and in production when the domain is known.
//
// What it knows for sure comes from LIKYLY (the placements and where each one shows, a few real
// items and their URL) and from the project (framework, store, dev port). A route it cannot know
// (a Next.js product page is /catalogs/[slug]/[id] in one project, /p/[handle] in another) is
// left null with the example item: the /likyly-pages command reads the code to fill it in.

import { detectProject } from "./frameworks.mjs";
import { readDeveloperKey } from "./cache.mjs";
import { CREDENTIALS_FILE, KEY_ENV } from "./mcp.mjs";
import { portTaken, servesShopify, themeStore, SHOPIFY_PREVIEW_PORT } from "./preview.mjs";
import { API_URL, wizardUserAgent } from "./verify.mjs";

/** The usual local port of each framework's dev server (what `npm run dev` prints by default). */
const DEV_PORTS = { nextjs: 3000, nuxt: 3000, remix: 5173, "react-cra": 3000, "react-vite": 5173, vue: 5173, sveltekit: 5173, astro: 4321, angular: 4200, laravel: 8000, symfony: 8000 };

const PAGE_LABELS = {
  product_page: "Product page",
  listing_page: "Listing page",
  category_page: "Category page",
  homepage: "Home page",
  cart: "Cart",
  account: "Account",
  content_page: "Content page",
  custom: "Custom block",
};

/** An item's URL, as a path on the site when it can be: absolute -> its path, "/x" -> as is. */
function itemPath(url) {
  if (!url || typeof url !== "string") return null;
  if (/^https?:\/\//i.test(url)) {
    try {
      const u = new URL(url);
      return u.pathname + u.search;
    } catch {
      return null;
    }
  }
  return url.startsWith("/") ? url : null;
}

/** The site's own domain from absolute item URLs, if the catalog carries them. */
function originOf(items) {
  for (const item of items) {
    const url = item.properties?.url;
    if (typeof url === "string" && /^https?:\/\//i.test(url)) {
      try {
        return new URL(url).origin;
      } catch {
        /* next */
      }
    }
  }
  return null;
}

/** The path of one example page of this kind, or null when only the code can tell. */
function examplePath(context, platform, item) {
  const url = item?.properties?.url;
  if (platform === "shopify") {
    if (context === "product_page") return item ? (itemPath(url) ?? (url ? `/products/${url}` : null)) : null;
    if (context === "category_page" || context === "listing_page") return "/collections/all";
    if (context === "homepage") return "/";
    if (context === "cart") return "/cart";
    if (context === "account") return "/account";
    return null;
  }
  if (context === "product_page") return item ? itemPath(url) : null;
  if (context === "homepage") return "/";
  return null;
}

/** Local and production base URLs: { local: {url, running}, production }. */
async function bases(project, dir, items, options) {
  if (project?.id === "shopify") {
    const store = await themeStore(dir, options);
    // A preview already running answers on 9292 (or the port given); the next one would too.
    const port = Number(options.port) || SHOPIFY_PREVIEW_PORT;
    const running = (await portTaken(port)) && (await servesShopify(port, options.storefrontFetch));
    return { platform: "shopify", local: { url: `http://127.0.0.1:${port}`, running, start: "npx @likyly/wizard preview" }, production: store ? `https://${store}` : null };
  }
  const port = DEV_PORTS[project?.id];
  const local = port ? { url: `http://localhost:${port}`, running: await portTaken(port), start: "npx @likyly/wizard preview" } : null;
  return { platform: project?.id ?? null, local, production: originOf(items) };
}

/**
 * Returns { ok: true, report } or { ok: false, text, hint } - never throws.
 * report: { project, local, production, pages: [{ block, name, context, label, catalog, enabled,
 *   path, local, production, example: {itemId, title, url, category} | null, note? }] }
 */
export async function examplePages({ dir, fetchImpl = fetch, ...options }) {
  const key = readDeveloperKey(dir);
  if (!key) return { ok: false, text: `No developer key: ${CREDENTIALS_FILE} is missing and ${KEY_ENV} is not set.`, hint: "Run npx @likyly/wizard in this project first." };
  const get = async (path) => {
    const res = await fetchImpl(`${API_URL}${path}`, { headers: { "X-API-Key": key, "User-Agent": wizardUserAgent("pages") }, signal: AbortSignal.timeout(15000) });
    return { status: res.status, ok: res.ok, data: await res.json().catch(() => null) };
  };
  try {
    const placements = await get("/placements");
    if (placements.status === 401 || placements.status === 403)
      return { ok: false, text: "This key cannot read your placements.", hint: "Run npx @likyly/wizard again to get a developer key." };
    if (!placements.ok) return { ok: false, text: `LIKYLY answered ${placements.status}: ${placements.data?.detail ?? "unexpected error"}` };

    // A few real items per catalog: one example page each, never the whole catalog.
    const catalogs = [...new Set(placements.data.map((p) => p.product_type).filter(Boolean))];
    const samples = {};
    let itemsRefused = false;
    for (const catalog of catalogs) {
      const items = await get(`/items?data_product_type=${encodeURIComponent(catalog)}&limit=3`);
      if (items.status === 401 || items.status === 403) itemsRefused = true;
      samples[catalog] = items.ok && Array.isArray(items.data) ? items.data : [];
    }

    const project = detectProject(dir);
    const base = await bases(project, dir, Object.values(samples).flat(), options);
    const pages = placements.data.map((p) => {
      const items = samples[p.product_type] ?? [];
      const item = items.find((i) => i.properties?.url) ?? items[0] ?? null;
      const path = examplePath(p.context_type, base.platform, item);
      const page = {
        block: p.slug,
        name: p.name,
        context: p.context_type,
        label: PAGE_LABELS[p.context_type] ?? p.context_type,
        catalog: p.product_type,
        enabled: p.enabled !== false,
        path,
        local: path && base.local ? base.local.url + path : null,
        production: path && base.production ? base.production + path : null,
        example: item ? { itemId: item.item_id, title: item.title, url: item.properties?.url ?? null, category: item.properties?.category ?? null } : null,
      };
      if (!items.length && ["product_page", "category_page", "listing_page"].includes(p.context_type))
        page.note = itemsRefused ? "this key cannot read the catalog (run npx @likyly/wizard again)" : `catalog ${p.product_type} has no items: this block has nothing to show`;
      else if (!path) page.note = "route not known from the data: read the code";
      return page;
    });
    return { ok: true, report: { project: project?.label ?? null, platform: base.platform, local: base.local, production: base.production, pages } };
  } catch {
    return { ok: false, text: `Could not reach the LIKYLY API (${API_URL}).`, hint: "Check your connection and try again." };
  }
}

/** The terminal version (the /likyly-pages command uses --json and reads the code for the rest). */
export function formatPages(r) {
  const lines = ["LIKYLY · Where to see it"];
  if (r.local) lines.push(`  Local       ${r.local.url}${r.local.running ? " · running" : ` · not running (start it: ${r.local.start})`}`);
  lines.push(`  Production  ${r.production ?? "unknown from LIKYLY's data"}`);
  const seen = new Set();
  for (const p of r.pages) {
    const key = `${p.context}|${p.block}`;
    if (seen.has(key)) continue;
    seen.add(key);
    lines.push("", `  ${p.label} · ${p.name} (${p.block})${p.enabled ? "" : " · disabled"}`);
    if (p.local) lines.push(`    local       ${p.local}`);
    if (p.production) lines.push(`    production  ${p.production}`);
    if (p.note) lines.push(`    · ${p.note}${p.example ? ` - example item ${p.example.itemId}${p.example.title ? ` (${p.example.title})` : ""}` : ""}`);
  }
  if (!r.pages.length) lines.push("", "  No recommendation block in LIKYLY yet.");
  return lines.join("\n");
}
