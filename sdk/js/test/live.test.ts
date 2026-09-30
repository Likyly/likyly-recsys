import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { AuthenticationError, Likyly, NotFoundError, PermissionDeniedError, ValidationError } from "../src/index.js";

/**
 * End-to-end against a REAL LIKYLY API (sdk/conformance/local-api.sh starts one). Skipped unless
 * LIKYLY_TEST_URL, LIKYLY_TEST_SECRET_KEY and LIKYLY_TEST_PUBLIC_KEY are set.
 */
const url = process.env.LIKYLY_TEST_URL;
const secretKey = process.env.LIKYLY_TEST_SECRET_KEY;
const publicKey = process.env.LIKYLY_TEST_PUBLIC_KEY;
const live = url && secretKey && publicKey ? { url, secretKey, publicKey } : undefined;

describe("live API", { skip: live ? false : "LIKYLY_TEST_* not set" }, () => {
  const catalog = `ts-${Date.now()}`;
  const server = () => new Likyly({ apiKey: live!.secretKey, baseUrl: live!.url, catalog });
  const browser = () => new Likyly({ apiKey: live!.publicKey, baseUrl: live!.url, catalog });
  const GID = "gid://shopify/Product/123456";

  it("items: upsert / get / list / delete, with ids of any shape", async () => {
    const likyly = server();
    const created = await likyly.items.upsert("SKU-123", { title: "Nike Air Max", description: "Running shoe", properties: { category: "shoes", brand: "Nike", price: 129.9 } });
    assert.equal(created.itemId, "SKU-123");
    assert.deepEqual(created.properties, { category: "shoes", brand: "Nike", price: 129.9 });

    await likyly.items.upsert(GID, { title: "Shopify boot", description: "warm winter boot", properties: { category: "boots" } });
    await likyly.items.upsert("3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", { title: "UUID shoe", description: "trail running shoe", properties: { category: "shoes" } });
    assert.equal((await likyly.items.get(GID)).title, "Shopify boot");

    const page = await likyly.items.list({ limit: 2, offset: 0 });
    assert.equal(page.items.length, 2);
    assert.equal(page.total, 3);

    const many = await likyly.items.upsertMany([
      { itemId: "SKU-A", title: "Adidas", description: "road running shoe", properties: { category: "shoes" } },
      { itemId: "SKU-B", title: "Asics", description: "road running shoe", properties: { category: "shoes" } },
    ]);
    assert.equal(many.succeeded, 2);

    await likyly.items.delete("SKU-B");
    await assert.rejects(() => likyly.items.get("SKU-B"), NotFoundError);
    const gone = await likyly.items.deleteMany(["SKU-A", "ghost"]);
    assert.deepEqual({ succeeded: gone.succeeded, failed: gone.failed, id: gone.errors[0]?.id }, { succeeded: 1, failed: 1, id: "ghost" });
    await likyly.items.upsert("SKU-A", { title: "Adidas", description: "road running shoe", properties: { category: "shoes" } });
  });

  it("users: upsert / get / list / delete with free-form properties", async () => {
    const likyly = server();
    const user = await likyly.users.upsert("user_123", { properties: { country: "FR", segment: "premium", language: "fr" } });
    assert.equal(user.userId, "user_123");
    assert.equal((await likyly.users.get("user_123")).properties.segment, "premium");
    assert.ok((await likyly.users.list({ limit: 10 })).users.some((u) => u.userId === "user_123"));
    assert.equal((await likyly.users.import([{ userId: "u_imp", properties: { country: "DE" } }])).succeeded, 1);
    await likyly.users.delete("u_imp");
    await assert.rejects(() => likyly.users.get("u_imp"), NotFoundError);
  });

  it("events: identified, anonymous, both, custom, idempotent purchase, batch - with the PUBLIC key", async () => {
    const likyly = browser();
    await likyly.events.view({ userId: "user_123", itemId: "SKU-123" });
    await likyly.events.view({ sessionId: "sess_123", itemId: "SKU-123" });
    await likyly.events.view({ userId: "user_789", sessionId: "sess_123", itemId: GID });
    await likyly.events.track("favorite", { userId: "user_123", itemId: "SKU-123", properties: { source: "wishlist" } });
    await likyly.events.addToCart({ userId: "user_123", itemId: "SKU-123", quantity: 1 });
    await likyly.events.removeFromCart({ userId: "user_123", itemId: "SKU-123", quantity: 1 });
    const purchase = { eventId: `purchase_${Date.now()}`, userId: "user_123", itemId: "SKU-123", quantity: 1, properties: { price: 129.9, currency: "EUR", orderId: "ORDER-9281" } };
    assert.equal((await likyly.events.purchase(purchase)).duplicate, false);
    assert.equal((await likyly.events.purchase(purchase)).duplicate, true);
    const batch = await likyly.events.trackMany([
      { type: "view", userId: "user_123", itemId: "SKU-A" },
      { type: "click", sessionId: "sess_123", itemId: "SKU-A" },
    ]);
    assert.deepEqual(batch, { received: 2, accepted: 2, duplicates: 0 });
  });

  it("recommendations: get in every context, then attribution through recommendationId", async () => {
    const likyly = browser();
    const forUser = await likyly.recommendations.get({ userId: "user_123", placement: "homepage", limit: 3 });
    assert.match(forUser.recommendationId, /^rec_[0-9A-Z]{26}$/);
    assert.equal(forUser.placement, "homepage");
    assert.ok(forUser.items.length > 0 && forUser.items.length <= 3);
    assert.equal((await likyly.recommendations.get({ itemId: "SKU-123", limit: 2 })).strategy, "content");
    assert.equal((await likyly.recommendations.get({ sessionId: "sess_123", viewedItemIds: ["SKU-123"], limit: 2 })).strategy, "session");
    assert.equal((await likyly.recommendations.get({ itemId: GID, limit: 2 })).strategy, "content"); // an id with "/" in the body
    const anything = await likyly.recommendations.get({ limit: 2 });
    assert.ok(anything.recommendationId);

    const shown = forUser.items[0]!;
    await likyly.events.impression({ userId: "user_123", itemId: shown.itemId, recommendationId: forUser.recommendationId, placement: "homepage" });
    const click = await likyly.events.click({ userId: "user_123", itemId: shown.itemId, recommendationId: forUser.recommendationId, placement: "homepage" });
    assert.equal(click.duplicate, false);
  });

  it("advanced recommendations answer with the same response type", async () => {
    const likyly = browser();
    assert.ok((await likyly.recommendations.popular({ limit: 2 })).recommendationId);
    const similar = await likyly.recommendations.similar({ itemId: "SKU-123", limit: 2 });
    assert.equal(similar.strategy, "content");
    assert.ok(similar.items[0]!.itemId);
    assert.ok((await likyly.recommendations.hybrid({ userId: "user_123", itemId: "SKU-123", limit: 2, alpha: 0.3 })).recommendationId);
    assert.equal((await likyly.recommendations.session({ viewedItemIds: ["SKU-123", "SKU-A"], limit: 2 })).strategy, "session");
    assert.ok((await likyly.recommendations.session({ userId: "user_123", limit: 2 })).recommendationId);
    await assert.rejects(() => likyly.recommendations.collaborative({ userId: "user_123", limit: 2 }), NotFoundError); // no trained model yet
  });

  it("the public key cannot manage the catalog or users; a wrong key is refused", async () => {
    await assert.rejects(() => browser().items.upsert("x", { title: "t" }), PermissionDeniedError);
    await assert.rejects(() => browser().items.list(), PermissionDeniedError);
    await assert.rejects(() => browser().users.get("user_123"), PermissionDeniedError);
    await assert.rejects(() => new Likyly({ apiKey: "nope", baseUrl: live!.url }).events.view({ userId: "u", itemId: "i" }), AuthenticationError);
  });

  it("API-side validation surfaces as ValidationError with the request id", async () => {
    await assert.rejects(
      () => server().items.list({ limit: 5000 }),
      (e: unknown) => e instanceof ValidationError && (e as ValidationError).statusCode === 422 && /^req_/.test((e as ValidationError).requestId ?? ""),
    );
  });

  it("cleanup", async () => {
    const likyly = server();
    await likyly.items.deleteMany(["SKU-123", GID, "3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", "SKU-A"]);
    await likyly.users.delete("user_123");
  });
});
