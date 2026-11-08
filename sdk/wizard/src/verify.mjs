// Checks run once everything is written: the SDK resolves, and each key is accepted by the API.

import { readFileSync } from "node:fs";
import { packageVersion } from "./compat.mjs";

export const API_URL = (process.env.LIKYLY_API_URL || "https://api.likyly.com").replace(/\/+$/, "");

const WIZARD_VERSION = (() => {
  try {
    return JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).version;
  } catch {
    return "0.0.0";
  }
})();

/**
 * What the wizard sends as User-Agent: its version and the framework it detected, so the LIKYLY
 * team can see which stacks people install on ("likyly-wizard/0.8.0 framework/nextjs").
 * Nothing else - no path, no project name.
 */
export function wizardUserAgent(frameworkId) {
  const framework = typeof frameworkId === "string" && /^[a-z0-9-]{1,32}$/.test(frameworkId) ? ` framework/${frameworkId}` : "";
  return `likyly-wizard/${WIZARD_VERSION}${framework}`;
}

/** `{ name, version }` for each SDK package found in node_modules (JS projects only). */
export function installedPackages(dir, packages) {
  return packages.map((name) => ({ name, version: packageVersion(dir, name)?.installed ? packageVersion(dir, name).version : null }));
}

/**
 * Ask the API whether it accepts `key`: "ok", "rejected" (401/403) or "unreachable".
 * Public keys are tried on a recommendations route, developer keys on the placements list.
 */
export async function checkKey(key, kind, frameworkId) {
  const path = kind === "developer" ? "/placements" : "/getRec/popular/1";
  try {
    const res = await fetch(`${API_URL}${path}`, {
      headers: { "X-API-Key": key, "User-Agent": wizardUserAgent(frameworkId) },
      signal: AbortSignal.timeout(8000),
    });
    return res.status === 401 || res.status === 403 ? "rejected" : "ok";
  } catch {
    return "unreachable";
  }
}
