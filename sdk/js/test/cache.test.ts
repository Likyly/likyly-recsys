import { test } from "node:test";
import assert from "node:assert/strict";
import { PausedError } from "../src/index.js";
import { RecommendCache } from "../src/recommend-cache.js";
import { makeClient } from "./helpers.js";

const ANSWER = { recommendation_id: "rec_1", placement: "pdp", strategy_used: "content", items: [{ item_id: "B", score: 0.9 }] };

test("a placement answer is reused for the same product, whoever the anonymous visitor is", async () => {
  const { client, calls } = makeClient([{ body: ANSWER }]);
  const first = await client.recommend({ placement: "pdp", context: { itemId: "A", sessionId: "s1" }, limit: 4 });
  const again = await client.recommend({ placement: "pdp", context: { itemId: "A", sessionId: "s2" }, limit: 4 });
  assert.equal(calls.length, 1, "the second page view did not call LIKYLY");
  assert.deepEqual(again, first);
  assert.equal(again.recommendationId, "rec_1", "impressions and clicks stay attributed");

  await client.recommend({ placement: "pdp", context: { itemId: "C" }, limit: 4 });
  await client.recommend({ placement: "pdp", context: { itemId: "A", userId: "u1" }, limit: 4 });
  assert.equal(calls.length, 3, "another product, or a signed-in visitor, is its own answer");

  client.clearRecommendationCache();
  await client.recommend({ placement: "pdp", context: { itemId: "A" }, limit: 4 });
  assert.equal(calls.length, 4);
});

test("cache: false calls LIKYLY every time", async () => {
  const { client, calls } = makeClient([{ body: ANSWER }], { cache: false });
  await client.recommend({ placement: "pdp", context: { itemId: "A" } });
  await client.recommend({ placement: "pdp", context: { itemId: "A" } });
  assert.equal(calls.length, 2);
});

test("after an outage, calls pause instead of waiting on LIKYLY, then resume", async () => {
  const { client, calls } = makeClient([{ status: 500, body: { detail: "boom" } }, { body: ANSWER }], { maxRetries: 0 });
  await assert.rejects(() => client.recommend({ placement: "pdp", context: { itemId: "A" } }));
  await assert.rejects(() => client.recommend({ placement: "pdp", context: { itemId: "B" } }), PausedError);
  assert.equal(calls.length, 1, "the paused call never reached the network");
});

test("a 4xx is a mistake in the call, not an outage: no pause", async () => {
  const { client, calls } = makeClient([{ status: 404, body: { detail: "No placement" } }, { body: ANSWER }], { maxRetries: 0 });
  await assert.rejects(() => client.recommend({ placement: "nope", context: {} }));
  await client.recommend({ placement: "pdp", context: { itemId: "A" } });
  assert.equal(calls.length, 2);
});

test("entries expire and the pause ends", () => {
  let now = 0;
  const cache = new RecommendCache({ ttlSeconds: 10, pauseSeconds: 5, storage: "memory" }, () => now);
  const value = { recommendationId: "r", placement: "p", strategyUsed: "content", items: [] } as never;
  cache.set("k", value);
  now = 9_000;
  assert.equal(cache.get("k"), value);
  now = 10_001;
  assert.equal(cache.get("k"), null);
  cache.recordFailure();
  assert.equal(cache.isPaused(), true);
  now = 15_002;
  assert.equal(cache.isPaused(), false);
});
