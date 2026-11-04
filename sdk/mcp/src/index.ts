#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { LikylyClient } from "./client.js";
import { createLikylyServer } from "./tools.js";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * The key, from the environment or else from the project's credentials file written by
 * `npx @likyly/wizard` (`.likyly/credentials.env`, gitignored): editors expand
 * `${LIKYLY_DEVELOPER_KEY}` to an empty string when it is not exported, so an empty or
 * unexpanded value falls through to the file.
 */
function resolveApiKey(): string | undefined {
  const fromEnv = process.env.LIKYLY_API_KEY?.trim();
  if (fromEnv && !fromEnv.startsWith("${")) return fromEnv;
  const file = resolve(process.env.LIKYLY_ENV_FILE || ".likyly/credentials.env");
  try {
    for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
      const m = line.match(/^\s*(LIKYLY_DEVELOPER_KEY|LIKYLY_API_KEY)\s*=\s*["']?([^"'\s#]+)/);
      if (m) return m[2];
    }
  } catch {
    // No credentials file: the error below explains how to provide a key.
  }
  return undefined;
}

const apiKey = resolveApiKey();
if (!apiKey) {
  console.error(
    "LIKYLY_API_KEY is not set. For recommendations/event tracking only, use your restricted " +
      "public key (account page's \"Clé publique\"). Running here in Claude Code, Codex or " +
      "another coding agent and want the data-source admin tools (create_data_source, " +
      "sync_data_source, ...) too? Use a developer key instead (create one with " +
      "POST /clients/me/developer-keys, scopes sources:read + sources:write) - never a secret " +
      "or developer key in a browser-facing environment, since it may not be a trusted one.",
  );
  process.exit(1);
}

const baseUrl = process.env.LIKYLY_BASE_URL ?? "https://api.likyly.com";
// Most LIKYLY tenants only ever manage one catalog namespace - this lets a vibe coder
// configure it once instead of repeating it in every prompt/tool call, while still
// allowing an override per-call for multi-catalog setups.
const defaultProductType = process.env.LIKYLY_PRODUCT_TYPE;

const server = createLikylyServer(new LikylyClient({ baseUrl, apiKey }), defaultProductType);
await server.connect(new StdioServerTransport());
