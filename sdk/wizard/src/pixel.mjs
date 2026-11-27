// `npx @likyly/wizard pixel`: the purchases of a Shopify theme. The checkout is Shopify's own page,
// where no theme code runs: only a pixel (Shopify admin → Settings → Customer events) sees the
// add to cart and the order. This prints that pixel, ready to paste, with the theme's public key.
//
// Attribution: a click in a LIKYLY block keeps [recommendationId, placement, time] per product in
// localStorage (likyly_rec, written by sections/likyly-recommendations.liquid, the same format as
// the LIKYLY Shopify app); the pixel's script reads it when that product is added to the cart or
// bought, so the purchase carries the recommendation_id - what LIKYLY's revenue counts (without
// it, LIKYLY looks for the visitor's last click on the product). Same browser and
// same domain only: a click on 127.0.0.1 (shopify theme dev) is not seen by the checkout on
// <store>.myshopify.com - test through the store's own address (the preview link).

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { API_URL } from "./verify.mjs";
import { themeStore } from "./preview.mjs";

const KEY_FILE = "snippets/likyly-key.liquid";

/** The public key the theme already uses, or null. */
export function themePublicKey(dir) {
  const file = join(dir, KEY_FILE);
  if (!existsSync(file)) return null;
  return readFileSync(file, "utf8").match(/pk_[A-Za-z0-9_-]+/)?.[0] ?? null;
}

/**
 * The custom pixel's code: a few lines that load LIKYLY's pixel script (served by the LIKYLY API,
 * /pixel/shopify.js) and hand it Shopify's analytics, the storage and the key. The events Shopify
 * publishes before the script arrives wait in `q`: no order is lost. Fixes reach every store
 * without pasting again.
 */
export function shopifyCustomPixel(key, apiUrl = API_URL) {
  const api = apiUrl.replace(/\/$/, "");
  const options = api === API_URL ? "{ analytics, browser, init, queue: q }" : `{ analytics, browser, init, queue: q, api: ${JSON.stringify(api)} }`;
  return `// LIKYLY - add to cart and purchases, for the revenue of the recommendations.
const q = [];
analytics.subscribe("product_added_to_cart", (e) => q.push(e));
analytics.subscribe("checkout_completed", (e) => q.push(e));
const s = document.createElement("script");
s.src = ${JSON.stringify(api + "/pixel/shopify.js")};
s.onload = () => Likyly.pixel(${JSON.stringify(key)}, ${options});
document.head.appendChild(s);
`;
}

/** Where the pixel is pasted, for this store (or the generic path). */
export function customerEventsUrl(store) {
  const handle = store?.replace(/\.myshopify\.com$/, "");
  return handle ? `https://admin.shopify.com/store/${handle}/settings/customer_events` : "Shopify admin → Settings → Customer events";
}

/** { ok: true, code, url } or { ok: false, text, hint }. */
export async function pixelFor(dir, options = {}) {
  const key = themePublicKey(dir);
  if (!key) return { ok: false, text: `No public key in ${KEY_FILE}: this folder is not a Shopify theme set up by LIKYLY.`, hint: "Run npx @likyly/wizard in the theme folder first." };
  const store = await themeStore(dir, options).catch(() => null);
  return { ok: true, code: shopifyCustomPixel(key), url: customerEventsUrl(store), store };
}
