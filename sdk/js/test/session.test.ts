import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import { LikylySession } from "../src/index.js";

function installFakeLocalStorage(): Storage {
  const store = new Map<string, string>();
  const storage = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: () => null,
    get length() {
      return store.size;
    },
  } as unknown as Storage;
  (globalThis as unknown as { localStorage?: Storage }).localStorage = storage;
  return storage;
}

function removeFakeLocalStorage() {
  delete (globalThis as unknown as { localStorage?: Storage }).localStorage;
}

describe("LikylySession - first-party, cookieless anonymous id", () => {
  afterEach(removeFakeLocalStorage);

  it("returns undefined with no localStorage (SSR / Node)", () => {
    const session = new LikylySession();
    assert.equal(session.getAnonymousId(), undefined);
  });

  it("generates and persists an id on first call, reuses the cached one after", () => {
    installFakeLocalStorage();
    const session = new LikylySession();
    const id = session.getAnonymousId();
    assert.ok(id?.startsWith("anon_"), `expected an "anon_" id, got ${id}`);
    assert.equal(session.getAnonymousId(), id);
  });

  it("a second instance reads the same id back from storage", () => {
    const storage = installFakeLocalStorage();
    const id = new LikylySession().getAnonymousId();
    assert.equal(new LikylySession().getAnonymousId(), id);
    assert.equal(storage.getItem("likyly_anonymous_id"), id);
  });

  it("respects a custom storageKey, so two LIKYLY-powered apps on one domain don't collide", () => {
    const storage = installFakeLocalStorage();
    new LikylySession({ storageKey: "my_app_anon_id" }).getAnonymousId();
    assert.ok(storage.getItem("my_app_anon_id"));
    assert.equal(storage.getItem("likyly_anonymous_id"), null);
  });

  it("degrades to undefined, never throws, when storage.setItem throws (private-mode quota)", () => {
    (globalThis as unknown as { localStorage: Storage }).localStorage = {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
      removeItem: () => {},
    } as unknown as Storage;
    assert.doesNotThrow(() => new LikylySession().getAnonymousId());
    assert.equal(new LikylySession().getAnonymousId(), undefined);
  });
});

describe("LikylySession.identify", () => {
  afterEach(removeFakeLocalStorage);

  it("calls events.identify with the anonymous id and the given userId", async () => {
    installFakeLocalStorage();
    const session = new LikylySession();
    const calls: unknown[] = [];
    const fakeLikyly = { events: { identify: async (input: unknown) => (calls.push(input), { linkedInteractions: 2 }) } };

    const result = await session.identify(fakeLikyly as never, "user_1");

    assert.deepEqual(calls[0], { userId: "user_1", sessionId: session.getAnonymousId() });
    assert.deepEqual(result, { linkedInteractions: 2 });
  });

  it("is a no-op (never calls events.identify) without a persistable anonymous id", async () => {
    const calls: unknown[] = [];
    const fakeLikyly = { events: { identify: async (input: unknown) => (calls.push(input), { linkedInteractions: 0 }) } };

    const result = await new LikylySession().identify(fakeLikyly as never, "user_1");

    assert.equal(result, undefined);
    assert.equal(calls.length, 0);
  });
});
