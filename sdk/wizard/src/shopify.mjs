// A Shopify store's catalog, connected by the wizard itself: the developer types the app's
// credentials in the terminal (the secret masked) and they go straight to the LIKYLY API, which
// stores them encrypted. They never reach the AI assistant, a file or the theme.

import { connectCatalog } from "./catalog.mjs";

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

/** Create (or, for the same shop, update) the `shopify` source, test it, start the first sync. */
export function connectShopifyCatalog({ apiKey, shop, credentials, ...rest }) {
  return connectCatalog({
    apiKey,
    platform: "Shopify",
    agent: "shopify",
    label: shop,
    matches: (s) => s.type === "shopify" && s.config?.shop_domain === shop,
    source: { name: `Shopify · ${shop}`, type: "shopify", product_type: catalogName(shop), config: { shop_domain: shop } },
    credentials,
    ...rest,
  });
}
