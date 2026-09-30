import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { RecommendationsController } from "../src/controller.js";

interface RecommendCall {
  placement: string;
  context?: unknown;
  limit?: number;
}

function fakeLikyly(recommendImpl: (call: RecommendCall) => Promise<unknown>) {
  const eventCalls: { method: string; input: unknown }[] = [];
  const client = {
    recommend: (call: RecommendCall) => recommendImpl(call),
    events: {
      recommendationImpression: async (input: unknown) => {
        eventCalls.push({ method: "recommendationImpression", input });
        return { message: "ok", duplicate: false };
      },
      recommendationClick: async (input: unknown) => {
        eventCalls.push({ method: "recommendationClick", input });
        return { message: "ok", duplicate: false };
      },
    },
  };
  return { client, eventCalls };
}

const RESULT = { recommendationId: "rec_1", placement: "pdp-related", strategyUsed: "content", items: [{ itemId: "SKU-1", properties: {} }] };

describe("RecommendationsController", () => {
  it("starts idle, then loading, then resolved", async () => {
    const { client } = fakeLikyly(async () => RESULT);
    const controller = new RecommendationsController(client as never);
    assert.deepEqual(controller.getState(), { items: [], isLoading: false });

    const pending = controller.run({ placement: "pdp-related", context: { itemId: "SKU-1" } });
    assert.equal(controller.getState().isLoading, true);
    await pending;

    assert.equal(controller.getState().isLoading, false);
    assert.equal(controller.getState().strategyUsed, "content");
    assert.equal(controller.getState().recommendationId, "rec_1");
    assert.equal(controller.getState().items.length, 1);
  });

  it("notifies subscribers on every state change", async () => {
    const { client } = fakeLikyly(async () => RESULT);
    const controller = new RecommendationsController(client as never);
    let notifications = 0;
    const unsubscribe = controller.subscribe(() => (notifications += 1));

    await controller.run({ placement: "pdp-related" });
    assert.ok(notifications >= 2); // at least: loading=true, then resolved

    unsubscribe();
    const before = notifications;
    await controller.run({ placement: "pdp-related" });
    assert.equal(notifications, before); // unsubscribed - no more notifications
  });

  it("enabled: false resets to idle and never calls recommend", async () => {
    let calls = 0;
    const { client } = fakeLikyly(async () => {
      calls += 1;
      return RESULT;
    });
    const controller = new RecommendationsController(client as never);
    await controller.run({ placement: "pdp-related", enabled: false });
    assert.equal(calls, 0);
    assert.deepEqual(controller.getState(), { items: [], isLoading: false });
  });

  it("a superseded call's response is dropped, not applied", async () => {
    const resolvers: ((value: unknown) => void)[] = [];
    const { client } = fakeLikyly(() => new Promise((resolve) => resolvers.push(resolve)));
    const controller = new RecommendationsController(client as never);

    const first = controller.run({ placement: "pdp-related", context: { itemId: "SKU-1" } });
    const second = controller.run({ placement: "pdp-related", context: { itemId: "SKU-2" } });

    resolvers[1]!({ ...RESULT, recommendationId: "rec_second" });
    await second;
    resolvers[0]!({ ...RESULT, recommendationId: "rec_first" });
    await first;

    assert.equal(controller.getState().recommendationId, "rec_second"); // the first (stale) response never overwrote it
  });

  it("a rejected recommend() call surfaces as state.error, not a thrown exception", async () => {
    const { client } = fakeLikyly(async () => {
      throw new Error("network down");
    });
    const controller = new RecommendationsController(client as never);
    await controller.run({ placement: "pdp-related" });
    assert.equal(controller.getState().error?.message, "network down");
    assert.equal(controller.getState().isLoading, false);
  });

  it("trackImpression/trackClick send the right event, attributed to the last recommend() call", async () => {
    const { client, eventCalls } = fakeLikyly(async () => RESULT);
    const controller = new RecommendationsController(client as never);
    await controller.run({ placement: "pdp-related", context: { itemId: "SKU-1", sessionId: "sess_1" } });

    controller.trackImpression("SKU-1");
    controller.trackClick("SKU-1");

    assert.deepEqual(eventCalls, [
      { method: "recommendationImpression", input: { itemId: "SKU-1", recommendationId: "rec_1", placement: "pdp-related", userId: undefined, sessionId: "sess_1" } },
      { method: "recommendationClick", input: { itemId: "SKU-1", recommendationId: "rec_1", placement: "pdp-related", userId: undefined, sessionId: "sess_1" } },
    ]);
  });

  it("tracking is a no-op before any result has arrived (nothing to attribute to yet)", async () => {
    const { client, eventCalls } = fakeLikyly(async () => RESULT);
    const controller = new RecommendationsController(client as never);
    controller.trackImpression("SKU-1");
    assert.equal(eventCalls.length, 0);
  });

  it("tracking is a no-op with neither a userId nor a sessionId in the context", async () => {
    const { client, eventCalls } = fakeLikyly(async () => RESULT);
    const controller = new RecommendationsController(client as never);
    await controller.run({ placement: "pdp-related", context: { itemId: "SKU-1" } }); // no user/session
    controller.trackClick("SKU-1");
    assert.equal(eventCalls.length, 0);
  });

  it("anonymousId in the context is used as the session for attribution when sessionId is absent", async () => {
    const { client, eventCalls } = fakeLikyly(async () => RESULT);
    const controller = new RecommendationsController(client as never);
    await controller.run({ placement: "pdp-related", context: { itemId: "SKU-1", anonymousId: "anon_1" } });
    controller.trackClick("SKU-1");
    assert.equal((eventCalls[0]!.input as { sessionId?: string }).sessionId, "anon_1");
  });
});
