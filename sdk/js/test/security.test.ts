import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import { Likyly, ValidationError } from "../src/index.js";
import { fakeFetch } from "./helpers.js";

/** A secret or developer key must never run where a browser could read this process's
 * memory/bundle - see client.ts's constructor guard. Simulated here by defining
 * `window`/`document` on globalThis, the same signal the guard itself checks. */
function installBrowserGlobals() {
  (globalThis as unknown as { window?: unknown }).window = {};
  (globalThis as unknown as { document?: unknown }).document = {};
}
function removeBrowserGlobals() {
  delete (globalThis as unknown as { window?: unknown }).window;
  delete (globalThis as unknown as { document?: unknown }).document;
}

describe("browser-unsafe key guard", () => {
  afterEach(removeBrowserGlobals);

  it("refuses a secret-shaped key (sk_...) when window+document are present", () => {
    installBrowserGlobals();
    const { fetch } = fakeFetch([]);
    assert.throws(() => new Likyly({ apiKey: "sk_abc123", fetch }), ValidationError);
  });

  it("refuses a developer-shaped key (lk_...) when window+document are present", () => {
    installBrowserGlobals();
    const { fetch } = fakeFetch([]);
    assert.throws(() => new Likyly({ apiKey: "lk_abc123", fetch }), ValidationError);
  });

  it("accepts the restricted public key (pk_...) in a browser", () => {
    installBrowserGlobals();
    const { fetch } = fakeFetch([]);
    assert.doesNotThrow(() => new Likyly({ apiKey: "pk_abc123", fetch }));
  });

  it("accepts a secret key server-side (no window/document) - the normal backend use case", () => {
    const { fetch } = fakeFetch([]);
    assert.doesNotThrow(() => new Likyly({ apiKey: "sk_abc123", fetch }));
  });

  it("never rejects a legacy, unprefixed key (issued before key prefixes existed)", () => {
    installBrowserGlobals();
    const { fetch } = fakeFetch([]);
    assert.doesNotThrow(() => new Likyly({ apiKey: "some-legacy-raw-token-with-no-prefix", fetch }));
  });
});
