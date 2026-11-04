// `npx @likyly/wizard cache clear [placement]`: empty LIKYLY's recommendation cache, one
// placement or all of them, with the project's developer key. A plain API call, no AI involved,
// so it is exact and scriptable (CI, deploy hooks).

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { CREDENTIALS_FILE, KEY_ENV } from "./mcp.mjs";
import { API_URL } from "./verify.mjs";

/** The developer key: exported LIKYLY_DEVELOPER_KEY first, else the project's credentials file. */
export function readDeveloperKey(dir) {
  const fromEnv = process.env[KEY_ENV]?.trim();
  if (fromEnv) return fromEnv;
  const path = join(dir, CREDENTIALS_FILE);
  if (!existsSync(path)) return null;
  const m = readFileSync(path, "utf8").match(/^\s*LIKYLY_DEVELOPER_KEY\s*=\s*["']?([^"'\s#]+)/m);
  return m ? m[1] : null;
}

/** Returns { ok, text, hint? } - never throws. */
export async function clearCache({ dir, placement }) {
  const key = readDeveloperKey(dir);
  if (!key) {
    return {
      ok: false,
      text: `No developer key: ${CREDENTIALS_FILE} is missing and ${KEY_ENV} is not set.`,
      hint: "Run npx @likyly/wizard in this project first, or export LIKYLY_DEVELOPER_KEY=lk_...",
    };
  }
  const path = placement ? `/placements/${encodeURIComponent(placement)}/cache/purge` : "/placements/cache/purge";
  let res;
  try {
    res = await fetch(`${API_URL}${path}`, { method: "POST", headers: { "X-API-Key": key }, signal: AbortSignal.timeout(10000) });
  } catch {
    return { ok: false, text: `Could not reach the LIKYLY API (${API_URL}).`, hint: "Check your connection and try again." };
  }
  const body = await res.json().catch(() => ({}));
  if (res.ok) {
    const n = typeof body.purged === "number" ? body.purged : 0;
    return {
      ok: true,
      text: `LIKYLY cache emptied · ${placement ? `placement ${placement}` : "every placement"} · ${n} cached answer${n === 1 ? "" : "s"} dropped`,
      hint: "The next page views are computed afresh. Your browser keeps its own copy per tab: open the page in a new tab to see it.",
    };
  }
  if (res.status === 404 && /placement/i.test(String(body.detail ?? "")))
    return { ok: false, text: `No placement "${placement}" in this workspace.`, hint: "See your placements: https://www.likyly.com/account/recommendations" };
  if (res.status === 404 || res.status === 405)
    return { ok: false, text: `This LIKYLY API (${API_URL}) does not offer cache clearing yet.`, hint: "It arrives with the next LIKYLY release." };
  if (res.status === 401 || res.status === 403)
    return { ok: false, text: "This key cannot empty the cache (it needs the placements:write right).", hint: "Run npx @likyly/wizard again to get a developer key." };
  return { ok: false, text: `LIKYLY answered ${res.status}: ${body.detail ?? "unexpected error"}` };
}
