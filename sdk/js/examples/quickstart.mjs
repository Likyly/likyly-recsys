// Quick start: catalog -> events -> recommendations -> attribution.
// Run it: LIKYLY_SECRET_KEY=... LIKYLY_PUBLIC_KEY=... node examples/quickstart.mjs
// region:imports
import { Likyly, NotFoundError, RateLimitError } from "@likyly/sdk";
// endregion

// region:initialize
// Backend only: the secret key gives access to your catalog and your users.
const likyly = new Likyly({
  apiKey: process.env.LIKYLY_SECRET_KEY,
  baseUrl: process.env.LIKYLY_BASE_URL, // docs:omit
  catalog: process.env.LIKYLY_CATALOG, // docs:omit
});
// endregion

// region:catalog
// Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
await likyly.items.upsertMany([
  { itemId: "SKU-1", title: "Nike Air Max", description: "Running shoe with air cushioning", properties: { category: "shoes", brand: "Nike", price: 129.9 } },
  { itemId: "SKU-2", title: "Adidas Ultraboost", description: "Responsive running shoe", properties: { category: "shoes", brand: "Adidas", price: 149 } },
  { itemId: "SKU-3", title: "Nike Pegasus", description: "Everyday running shoe", properties: { category: "shoes", brand: "Nike", price: 119 } },
]);

// Users are optional: describe them if you want the profile to travel with their events.
await likyly.users.upsert("user_123", { properties: { country: "FR", segment: "premium" } });
// endregion

// region:track
// Tell LIKYLY what your visitors do.
await likyly.events.view({ userId: "user_123", itemId: "SKU-1" });

// Purchases carry an eventId: replaying the call can never count the sale twice.
await likyly.events.purchase({
  eventId: "purchase_order_9281_SKU-1",
  userId: "user_123",
  itemId: "SKU-1",
  quantity: 1,
  properties: { price: 129.9, currency: "EUR", orderId: "order_9281" },
});
// endregion

// region:recommend
// Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
const recs = await likyly.recommendations.get({ userId: "user_123", placement: "homepage", limit: 3 });

console.log(`strategy: ${recs.strategy}`);
for (const item of recs.items) console.log(item.itemId, item.title);
// endregion

// region:showcase
// Les recommandations pour votre page : tout est optionnel, envoyez ce que vous savez.
const productRecs = await likyly.recommendations.get({
  userId: "user_123", // visiteur connecté
  sessionId: "sess_abc", // ou visiteur anonyme (cookie)
  itemId: "SKU-1", // fiche produit en cours de consultation
  placement: "product_page", // où elles seront affichées (libre)
  limit: 6, // combien d'articles (10 par défaut)
});

for (const item of productRecs.items) console.log(item.itemId, item.title, item.score);
// endregion

// region:attribution
// Send the recommendationId back: LIKYLY measures which recommendations get seen, clicked and bought.
await likyly.events.impression({ userId: "user_123", itemId: recs.items[0].itemId, recommendationId: recs.recommendationId, placement: "homepage" });
await likyly.events.click({ userId: "user_123", itemId: recs.items[0].itemId, recommendationId: recs.recommendationId, placement: "homepage" });
// endregion

// region:browser
// In a web page, use the PUBLIC key: it can only read recommendations and record events.
const browser = new Likyly({
  apiKey: process.env.LIKYLY_PUBLIC_KEY,
  baseUrl: process.env.LIKYLY_BASE_URL, // docs:omit
  catalog: process.env.LIKYLY_CATALOG, // docs:omit
});

// A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
await browser.events.view({ sessionId: "sess_abc", itemId: "SKU-2" });
const forVisitor = await browser.recommendations.get({ sessionId: "sess_abc", viewedItemIds: ["SKU-2"], placement: "product_page", limit: 3 });
// endregion

// region:custom
// Any string is a valid event type: track what matters to your business.
await likyly.events.track("favorite", { userId: "user_123", itemId: "SKU-2", properties: { list: "wishlist" } });

// Up to 1000 events per call, each with its own type.
await likyly.events.trackMany([
  { type: "view", userId: "user_123", itemId: "SKU-3" },
  { type: "add_to_cart", userId: "user_123", itemId: "SKU-3", quantity: 1 },
]);
// endregion

// region:advanced
// Advanced Recommendations: one strategy at a time, when you want to choose.
const similar = await likyly.recommendations.similar({ itemId: "SKU-1", limit: 3 });
const hybrid = await likyly.recommendations.hybrid({ userId: "user_123", itemId: "SKU-1", alpha: 0.7, limit: 3 });
const session = await likyly.recommendations.session({ viewedItemIds: ["SKU-1", "SKU-2"], limit: 3 });
// endregion

// region:config
const tuned = new Likyly({
  apiKey: process.env.LIKYLY_SECRET_KEY,
  timeout: 5_000, // milliseconds per attempt (default 10 000)
  maxRetries: 3, // automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
  userAgent: "my-shop/1.4", // appended to the SDK's User-Agent
  baseUrl: process.env.LIKYLY_BASE_URL, // docs:omit
});
// endregion

// region:errors
try {
  await likyly.items.get("does-not-exist");
} catch (error) {
  if (error instanceof NotFoundError) console.log("no such item, request", error.requestId);
  else if (error instanceof RateLimitError) console.log("slow down, retry in", error.retryAfter, "s");
  else throw error;
}
// endregion

console.log(`visitor strategy: ${forVisitor.strategy}`);

// region:cleanup
await likyly.items.deleteMany(["SKU-1", "SKU-2", "SKU-3"]);
await likyly.users.delete("user_123");
// endregion
console.log("quickstart ok");
