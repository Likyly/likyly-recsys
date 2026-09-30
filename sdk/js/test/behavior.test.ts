import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { Likyly, ApiError, NetworkError, RateLimitError, TimeoutError, ValidationError, LikylyError, AuthenticationError } from "../src/index.js";
import { makeClient } from "./helpers.js";

const REC = { recommendation_id: "rec_1", strategy: "popular", items: [] };
const EVT = { message: "ok", event_id: null, duplicate: false };

describe("client configuration", () => {
  it("requires an apiKey", () => {
    assert.throws(() => new Likyly({ apiKey: "" }), ValidationError);
    assert.throws(() => new Likyly({} as never), ValidationError);
  });
  it("defaults to the production API and strips a trailing slash from baseUrl", async () => {
    const a = makeClient([{ body: [] }], { baseUrl: undefined });
    await a.client.items.list();
    assert.equal(a.calls[0]!.url.origin, "https://api.likyly.com");
    const b = makeClient([{ body: [] }], { baseUrl: "https://proxy.example.test/likyly///" });
    await b.client.items.list();
    assert.equal(b.calls[0]!.url.pathname, "/likyly/items");
  });
  it("appends a custom userAgent to the SDK's own", async () => {
    const { client, calls } = makeClient([{ body: [] }], { userAgent: "my-shop/1.4" });
    await client.items.list();
    assert.match(calls[0]!.headers["user-agent"]!, /^likyly-typescript\/\d+\.\d+\.\d+ my-shop\/1\.4$/);
  });
});

describe("request validation (before anything is sent)", () => {
  it("an event needs an item and a user or a session", async () => {
    const { client, calls } = makeClient([{ body: EVT }]);
    await assert.rejects(() => client.events.view({ itemId: "a" }), ValidationError);
    await assert.rejects(() => client.events.view({ userId: "u" } as never), ValidationError);
    await assert.rejects(() => client.events.view({ itemId: "", userId: "u" }), ValidationError);
    assert.equal(calls.length, 0);
  });
  it("ids are strings, never coerced", async () => {
    const { client, calls } = makeClient([{ body: EVT }]);
    await assert.rejects(() => client.events.view({ itemId: 42 as never, userId: "u" }), ValidationError);
    await assert.rejects(() => client.items.get(7 as never), ValidationError);
    assert.equal(calls.length, 0);
  });
  it("event types are open strings but must be URL-safe", async () => {
    const { client } = makeClient([{ body: EVT }]);
    await client.events.track("favorite", { userId: "u", itemId: "i" });
    await assert.rejects(() => client.events.track("bad type!", { userId: "u", itemId: "i" }), ValidationError);
  });
  it("advanced endpoints refuse an id containing '/' (it cannot travel in the URL path)", async () => {
    const { client, calls } = makeClient([{ body: REC }]);
    await assert.rejects(() => client.recommendations.similar({ itemId: "gid://shopify/Product/1" }), /recommendations\.get\(\)/);
    await client.recommendations.get({ itemId: "gid://shopify/Product/1" }); // the body-based call accepts it
    assert.equal(calls.length, 1);
  });
  it("session() needs exactly one of viewedItemIds / userId", async () => {
    const { client } = makeClient([{ body: REC }]);
    await assert.rejects(() => client.recommendations.session({}), ValidationError);
    await assert.rejects(() => client.recommendations.session({ viewedItemIds: ["a"], userId: "u" }), ValidationError);
    await assert.rejects(() => client.recommendations.session({ viewedItemIds: [] }), ValidationError);
  });
  it("batches must not be empty", async () => {
    const { client } = makeClient([{ body: EVT }]);
    await assert.rejects(() => client.items.upsertMany([]), ValidationError);
    await assert.rejects(() => client.events.trackMany([]), ValidationError);
  });
  it("Date is serialized to ISO 8601", async () => {
    const { client, calls } = makeClient([{ body: EVT }]);
    await client.events.view({ userId: "u", itemId: "i", occurredAt: new Date("2026-09-24T10:30:00Z") });
    assert.equal((calls[0]!.body as { occurred_at: string }).occurred_at, "2026-09-24T10:30:00.000Z");
  });
  it("`properties` keys are never renamed, at any depth", async () => {
    const { client, calls } = makeClient([{ body: EVT }]);
    await client.events.purchase({ userId: "u", itemId: "i", properties: { orderId: "O-1", nested: { snakeCase_and_camelCase: 1 } } });
    assert.deepEqual((calls[0]!.body as { properties: unknown }).properties, { orderId: "O-1", nested: { snakeCase_and_camelCase: 1 } });
  });
});

describe("errors", () => {
  it("are all LikylyErrors; the hierarchy is usable", async () => {
    const { client } = makeClient([{ status: 401, body: { detail: "nope", request_id: "req_x" } }], { maxRetries: 0 });
    await assert.rejects(() => client.items.get("a"), (e: unknown) => e instanceof AuthenticationError && e instanceof ApiError && e instanceof LikylyError && e instanceof Error);
  });
  it("a 422 lists the offending fields in the message", async () => {
    const { client } = makeClient([{ status: 422, body: { detail: [{ loc: ["body", "title"], msg: "Field required" }], request_id: "r" } }], { maxRetries: 0 });
    await assert.rejects(() => client.items.get("a"), /Field required/);
  });
  it("a non-JSON error body (a proxy's HTML page) still becomes an ApiError with the status", async () => {
    const { fetch } = { fetch: (async () => new Response("<html>Bad gateway</html>", { status: 502 })) as typeof globalThis.fetch };
    const client = new Likyly({ apiKey: "k", fetch, maxRetries: 0 });
    await assert.rejects(() => client.items.get("a"), (e: unknown) => e instanceof ApiError && (e as ApiError).statusCode === 502);
  });
  it("network failures become NetworkError with the cause", async () => {
    const cause = new TypeError("fetch failed");
    const { client } = makeClient([{ fail: cause }], { maxRetries: 0 });
    await assert.rejects(() => client.items.get("a"), (e: unknown) => e instanceof NetworkError && (e as NetworkError).cause === cause);
  });
  it("a hanging request becomes TimeoutError", async () => {
    const { client } = makeClient([{ hang: true }], { timeout: 20, maxRetries: 0 });
    await assert.rejects(() => client.items.get("a"), TimeoutError);
  });
  it("the caller's own AbortSignal cancels the call and surfaces their abort", async () => {
    const { client } = makeClient([{ hang: true }], { timeout: 5_000, maxRetries: 0 });
    const controller = new AbortController();
    const pending = client.items.get("a", { signal: controller.signal });
    controller.abort();
    await assert.rejects(pending, (e: unknown) => (e as Error).name === "AbortError" && !(e instanceof LikylyError));
  });
});

describe("retries", () => {
  it("429 is retried for every request, honoring Retry-After", async () => {
    const { client, calls, sleeps } = makeClient([{ status: 429, headers: { "retry-after": "3" }, body: { detail: "slow down" } }, { body: EVT }]);
    const result = await client.events.view({ userId: "u", itemId: "i" }); // no eventId: still safe, 429 never reached the app
    assert.equal(result.duplicate, false);
    assert.equal(calls.length, 2);
    assert.deepEqual(sleeps, [3000]);
  });
  it("a Retry-After beyond a minute is not waited for: RateLimitError is thrown", async () => {
    const { client, calls } = makeClient([{ status: 429, headers: { "retry-after": "600" }, body: { detail: "x" } }]);
    await assert.rejects(() => client.items.get("a"), (e: unknown) => e instanceof RateLimitError && (e as RateLimitError).retryAfter === 600);
    assert.equal(calls.length, 1);
  });
  it("exponential backoff with jitter, then gives up after maxRetries", async () => {
    const { client, calls, sleeps } = makeClient([{ status: 503, body: { detail: "down" } }], { maxRetries: 3 });
    await assert.rejects(() => client.items.get("a"), ApiError);
    assert.equal(calls.length, 4);
    assert.deepEqual(sleeps, [500, 1000, 2000]); // random() pinned to 1 by the test hook
  });
  it("idempotent calls (reads, upserts, deletes) are retried on 502/503/504", async () => {
    for (const status of [502, 503, 504]) {
      const { client, calls } = makeClient([{ status, body: { detail: "x" } }, { body: { item_id: "a", title: "t" } }]);
      await client.items.upsert("a", { title: "t" });
      assert.equal(calls.length, 2, `status ${status}`);
    }
  });
  it("an event WITHOUT eventId is never retried on an ambiguous failure - no silent duplicates", async () => {
    for (const script of [[{ status: 503, body: { detail: "x" } }], [{ fail: new TypeError("reset") }], [{ hang: true }]]) {
      const { client, calls } = makeClient([...script, { body: EVT }], { timeout: 20 });
      await assert.rejects(() => client.events.purchase({ userId: "u", itemId: "i" }));
      assert.equal(calls.length, 1);
    }
  });
  it("an event WITH eventId is retried: the replay is harmless", async () => {
    const { client, calls } = makeClient([{ status: 503, body: { detail: "x" } }, { body: { ...EVT, event_id: "e1", duplicate: true } }]);
    const result = await client.events.purchase({ eventId: "e1", userId: "u", itemId: "i" });
    assert.equal(calls.length, 2);
    assert.equal(result.duplicate, true);
  });
  it("trackMany is retried only if every event has an eventId", async () => {
    const batch = { received: 2, accepted: 2, duplicates: 0 };
    const a = makeClient([{ status: 503, body: { detail: "x" } }, { body: batch }]);
    await assert.rejects(() => a.client.events.trackMany([{ type: "view", userId: "u", itemId: "1" }, { type: "view", userId: "u", itemId: "2", eventId: "e" }]));
    assert.equal(a.calls.length, 1);
    const b = makeClient([{ status: 503, body: { detail: "x" } }, { body: batch }]);
    await b.client.events.trackMany([{ type: "view", userId: "u", itemId: "1", eventId: "e1" }, { type: "view", userId: "u", itemId: "2", eventId: "e2" }]);
    assert.equal(b.calls.length, 2);
  });
  it("client errors (400-499 except 429) are never retried", async () => {
    for (const status of [400, 401, 403, 404, 422]) {
      const { client, calls } = makeClient([{ status, body: { detail: "x" } }, { body: {} }]);
      await assert.rejects(() => client.items.get("a"));
      assert.equal(calls.length, 1, `status ${status}`);
    }
  });
  it("per-call maxRetries and timeout override the client's", async () => {
    const { client, calls } = makeClient([{ status: 503, body: { detail: "x" } }], { maxRetries: 5 });
    await assert.rejects(() => client.items.get("a", { maxRetries: 0 }));
    assert.equal(calls.length, 1);
  });
});

describe("lists", () => {
  it("reports the catalog total from X-Total-Count and the pagination it used", async () => {
    const { client } = makeClient([{ body: [{ item_id: "a", title: "A" }], headers: { "x-total-count": "42" } }]);
    const page = await client.items.list({ limit: 1, offset: 10 });
    assert.deepEqual({ total: page.total, limit: page.limit, offset: page.offset }, { total: 42, limit: 1, offset: 10 });
  });
  it("sends only what you asked for - the API's defaults stay the API's", async () => {
    const { client, calls } = makeClient([{ body: [] }]);
    const page = await client.items.list();
    assert.deepEqual(Object.fromEntries(calls[0]!.url.searchParams), {});
    assert.deepEqual({ limit: page.limit, offset: page.offset }, { limit: undefined, offset: 0 });
  });
});
