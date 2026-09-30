import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Likyly, type LikylyOptions } from "../src/index.js";

export interface Recorded {
  method: string;
  url: URL;
  headers: Record<string, string>;
  body: unknown;
}

export interface Scripted {
  status?: number;
  headers?: Record<string, string>;
  body?: unknown;
  /** Throw instead of answering (a network failure). */
  fail?: Error;
  /** Never answer until aborted (a hang). */
  hang?: boolean;
}

/** A fake fetch that replays scripted responses in order (the last one repeats) and records every request. */
export function fakeFetch(script: Scripted[]) {
  const calls: Recorded[] = [];
  let index = 0;
  const impl = (async (input: string | URL | Request, init?: RequestInit) => {
    const step = script[Math.min(index, script.length - 1)] ?? {};
    index += 1;
    const url = new URL(String(input));
    const headers: Record<string, string> = {};
    for (const [k, v] of Object.entries((init?.headers ?? {}) as Record<string, string>)) headers[k.toLowerCase()] = v;
    calls.push({ method: String(init?.method), url, headers, body: init?.body === undefined ? undefined : JSON.parse(String(init.body)) });
    if (step.hang) {
      await new Promise((_, reject) => init?.signal?.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }))));
    }
    if (step.fail) throw step.fail;
    const payload = step.body === undefined || step.body === null ? null : JSON.stringify(step.body);
    return new Response(payload, { status: step.status ?? 200, headers: { "content-type": "application/json", ...(step.headers ?? {}) } });
  }) as typeof fetch;
  return { fetch: impl, calls };
}

export function makeClient(script: Scripted[], options: Partial<LikylyOptions> = {}) {
  const { fetch, calls } = fakeFetch(script);
  const sleeps: number[] = [];
  const client = new Likyly({
    apiKey: "sk_test_conformance",
    baseUrl: "https://api.example.test",
    fetch,
    maxRetries: 2,
    ...options,
    _internal: { sleep: async (ms) => void sleeps.push(ms), random: () => 1 }, // no real waiting; jitter = 1 -> the full backoff
  });
  return { client, calls, sleeps };
}

export interface Scenario {
  id: string;
  call: { resource: string; method: string; args: unknown[] };
  request: { method: string; path: string; query: Record<string, string>; body?: unknown };
  response: { status: number; headers: Record<string, string>; body: unknown };
  expect?: Record<string, unknown>;
  config?: { catalog?: string };
  error?: { class: string; statusCode: number; requestId?: string; retryAfter?: number; message?: string };
}

export function loadScenarios(): { defaults: { apiKey: string; baseUrl: string; userAgentPrefix: string }; scenarios: Scenario[] } {
  return JSON.parse(readFileSync(resolve(process.cwd(), "../conformance/scenarios.json"), "utf8"));
}

/** "items.0.explanation.reason" -> value; "items.length" works too. */
export function pick(obj: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((acc, key) => (acc === undefined || acc === null ? undefined : (acc as Record<string, unknown>)[key]), obj);
}
