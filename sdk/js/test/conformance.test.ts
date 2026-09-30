import assert from "node:assert/strict";
import { describe, it } from "node:test";
import * as sdk from "../src/index.js";
import { loadScenarios, makeClient, pick, type Scenario } from "./helpers.js";

/**
 * Runs the language-neutral scenarios in sdk/conformance/scenarios.json - the same file every SDK
 * runs, and that validate_against_openapi.py checks against the OpenAPI document. Each scenario
 * pins the exact HTTP request an SDK call must produce and how the response maps back.
 */
const { defaults, scenarios } = loadScenarios();

describe("conformance scenarios", () => {
  for (const scenario of scenarios) {
    it(scenario.id, async () => {
      const { client, calls } = makeClient([scenario.response], { apiKey: defaults.apiKey, catalog: scenario.config?.catalog, maxRetries: 0 });
      const resource = (client as unknown as Record<string, Record<string, (...a: unknown[]) => Promise<unknown>>>)[scenario.call.resource];
      assert.ok(resource, `no resource ${scenario.call.resource}`);
      const found = resource[scenario.call.method];
      assert.equal(typeof found, "function", `${scenario.call.resource}.${scenario.call.method} does not exist`);
      const method = found as (...a: unknown[]) => Promise<unknown>;

      if (scenario.error) {
        await assert.rejects(
          () => method.apply(resource, scenario.call.args),
          (e: unknown) => {
            const err = e as sdk.LikylyError;
            assert.equal(err.constructor.name, scenario.error!.class);
            assert.ok(err instanceof sdk.LikylyError);
            assert.equal(err.statusCode, scenario.error!.statusCode);
            if (scenario.error!.requestId) assert.equal(err.requestId, scenario.error!.requestId);
            if (scenario.error!.retryAfter !== undefined) assert.equal(err.retryAfter, scenario.error!.retryAfter);
            if (scenario.error!.message) assert.equal(err.message, scenario.error!.message);
            return true;
          },
        );
      } else {
        const result = await method.apply(resource, scenario.call.args);
        for (const [path, want] of Object.entries(scenario.expect ?? {})) {
          assert.deepEqual(pick(result, path), want, `${scenario.id}: result.${path}`);
        }
      }
      assertRequest(scenario, calls, defaults);
    });
  }
});

function assertRequest(scenario: Scenario, calls: ReturnType<typeof makeClient>["calls"], defaults: { apiKey: string; baseUrl: string; userAgentPrefix: string }) {
  assert.equal(calls.length, 1, "exactly one HTTP request");
  const call = calls[0]!;
  assert.equal(call.method, scenario.request.method);
  assert.equal(call.url.origin, defaults.baseUrl);
  assert.equal(call.url.pathname, scenario.request.path, "raw request path");
  assert.deepEqual(Object.fromEntries(call.url.searchParams), scenario.request.query, "query string");
  assert.equal(call.headers["x-api-key"], defaults.apiKey);
  assert.ok(call.headers["user-agent"]?.startsWith(defaults.userAgentPrefix), "user agent");
  assert.equal(call.headers["content-type"], scenario.request.body === undefined ? undefined : "application/json");
  assert.deepEqual(call.body, scenario.request.body, "request body");
}
