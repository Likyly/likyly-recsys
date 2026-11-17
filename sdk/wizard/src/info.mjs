// `npx @likyly/wizard info`: which LIKYLY account this project is connected to - the account and
// workspace, the key it uses (never its value), the plan, and every limit of Free and Pro side by
// side with what is used today. A plain API call (GET /analytics/plan) with the developer key: the
// numbers are the limits the API enforces, never hard-coded here, so they cannot drift.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { readDeveloperKey } from "./cache.mjs";
import { CREDENTIALS_FILE, KEY_ENV } from "./mcp.mjs";
import { API_URL, wizardUserAgent } from "./verify.mjs";

/** Every limit, in the order shown, with what it counts. `used` names the usage field when LIKYLY measures it. */
export const LIMITS = [
  { key: "product_limit", label: "Products in the catalog", used: "product_count" },
  { key: "index_limit", label: "Catalogs", used: "index_count" },
  { key: "data_source_limit", label: "Catalog sources (Shopify, Magento, feed...)", used: "data_source_count" },
  { key: "manual_sync_daily_limit", label: "Catalog syncs per day, per source" },
  { key: "manual_training_daily_limit", label: "Model trainings per day, per catalog" },
  { key: "auto_retrain_daily_limit", label: "Automatic retrains per day" },
  { key: "workspace_limit", label: "Workspaces" },
  { key: "analytics_window_days", label: "Performance history", unit: "days" },
  { key: "analytics_detail", label: "Daily trend, revenue detail, top products", bool: true },
];

const PLAN_NAMES = { free: "Free", pro: "Pro", unlimited: "Unlimited" };

/** The comment lines the wizard writes at the top of .likyly/credentials.env (never the key line). */
export function readAccountLines(dir) {
  const path = join(dir, CREDENTIALS_FILE);
  if (!existsSync(path)) return {};
  const out = {};
  for (const m of readFileSync(path, "utf8").matchAll(/^#\s*(Account|Workspace|Console):\s*(.+?)\s*$/gm)) out[m[1].toLowerCase()] = m[2];
  return out;
}

/**
 * Returns { ok: true, report } or { ok: false, text, hint } - never throws.
 * report: { account, workspace, key, plan, planName, plans[], limits[], signals[], upgradeUrl, console }
 */
export async function readInfo({ dir, fetchImpl = fetch }) {
  const key = readDeveloperKey(dir);
  if (!key) {
    return { ok: false, text: `No developer key: ${CREDENTIALS_FILE} is missing and ${KEY_ENV} is not set.`, hint: "Run npx @likyly/wizard in this project first, or export LIKYLY_DEVELOPER_KEY=lk_..." };
  }
  const local = readAccountLines(dir);
  let res;
  try {
    const r = await fetchImpl(`${API_URL}/analytics/plan`, { headers: { "X-API-Key": key, "User-Agent": wizardUserAgent("info") }, signal: AbortSignal.timeout(15000) });
    res = { status: r.status, ok: r.ok, data: await r.json().catch(() => ({})) };
  } catch {
    return { ok: false, text: `Could not reach the LIKYLY API (${API_URL}).`, hint: "Check your connection and try again." };
  }
  if (res.status === 401) return { ok: false, text: "LIKYLY does not know this developer key (revoked, or from another account).", hint: "Run npx @likyly/wizard again to sign in and get a new one." };
  if (res.status === 403) return { ok: false, text: "This key cannot read the account's plan (it needs the analytics:read right).", hint: "Run npx @likyly/wizard again to get a developer key." };
  if (!res.ok) return { ok: false, text: `LIKYLY answered ${res.status}: ${res.data.detail ?? "unexpected error"}` };

  const d = res.data;
  const plan = d.plan ?? "free";
  // An API older than this command answers without `plans`: show the account's own plan only.
  const plans = (d.plans?.length ? d.plans : [{ ...(d.limits ?? {}), plan }]).map((p) => ({ plan: p.plan, name: PLAN_NAMES[p.plan] ?? p.plan, current: p.plan === plan, values: p }));
  const usage = d.usage ?? {};
  const limits = LIMITS.map(({ key: k, label, used, unit, bool }) => {
    const row = { key: k, label, values: Object.fromEntries(plans.map((p) => [p.plan, p.values[k] ?? null])) };
    if (unit) row.unit = unit;
    if (bool) row.included = true;
    if (used && usage[used] !== undefined) row.used = usage[used];
    return row;
  });
  const report = {
    account: { email: local.account ?? null, console: local.console ?? "https://www.likyly.com" },
    workspace: d.workspace ? { id: d.workspace.id, name: d.workspace.name, createdAt: d.workspace.created_at } : local.workspace ? { name: local.workspace } : null,
    key: { ...(d.key ?? { kind: "developer" }), from: process.env[KEY_ENV]?.trim() ? `${KEY_ENV} (environment)` : CREDENTIALS_FILE },
    plan,
    planName: d.plan_name ?? PLAN_NAMES[plan] ?? plan,
    paid: plan !== "free",
    plans: plans.map(({ plan: p, name, current }) => ({ plan: p, name, current })),
    limits,
    upgradeUrl: d.upgrade_url ?? "https://likyly.com/account/upgrade",
    comparedOnlyOwnPlan: !d.plans?.length,
  };
  report.signals = signals(report);
  return { ok: true, report };
}

/** Where the account stands against its own plan: a limit used up ("full"), or close to it ("near") -
 * not an error (one catalog out of one on Free is normal), something to know before adding more. */
function signals({ limits, plan, planName }) {
  const out = [];
  for (const l of limits) {
    const cap = l.values[plan];
    if (l.used === undefined || cap === null || cap === undefined || !cap) continue;
    if (l.used >= cap) out.push({ level: "full", text: `${l.label}: ${l.used} / ${cap} - all that the ${planName} plan allows.` });
    else if (l.used / cap >= 0.8) out.push({ level: "near", text: `${l.label}: ${l.used} / ${cap} - close to what the ${planName} plan allows.` });
  }
  return out;
}

/** One limit, in words: "50", "no limit", "7 days", "yes"/"no". */
export function limitText(row, value) {
  if (row.included) return value ? "yes" : "no";
  if (value === null || value === undefined) return "no limit";
  return row.unit ? `${value} ${row.unit}` : String(value);
}

/** The terminal version (the /likyly-info command uses --json and writes its own report). */
export function formatInfo(r) {
  const keyText = r.key.kind === "secret" ? "the secret key" : `developer key${r.key.name ? ` "${r.key.name}"` : ""}${r.key.hint ? ` (${r.key.hint})` : ""}`;
  const lines = [
    `LIKYLY · Account · ${r.planName} plan${r.paid ? "" : " (free)"}`,
    `  Account     ${r.account.email ?? "(not recorded in this project)"}`,
    `  Workspace   ${r.workspace ? `${r.workspace.name}${r.workspace.id && r.workspace.id !== r.workspace.name ? ` · ${r.workspace.id}` : ""}` : "(unknown)"}`,
    `  Key         ${keyText} · from ${r.key.from}${r.key.scopes?.length ? `\n              rights: ${r.key.scopes.join(", ")}` : ""}`,
    "",
  ];
  const cols = r.plans;
  const width = Math.max(...r.limits.map((l) => l.label.length)) + 2;
  lines.push(`  ${"".padEnd(width)}${cols.map((p) => `${p.name}${p.current ? " ◆" : ""}`.padEnd(14)).join("")}Used`);
  for (const l of r.limits) lines.push(`  ${l.label.padEnd(width)}${cols.map((p) => limitText(l, l.values[p.plan]).padEnd(14)).join("")}${l.used ?? ""}`);
  if (r.comparedOnlyOwnPlan) lines.push("  · This LIKYLY API shows only your own plan's limits.");
  for (const s of r.signals) lines.push(`  ${s.level === "full" ? "!" : "·"} ${s.text}`);
  if (r.plan === "free") lines.push("", `  No online payment yet: request the Pro plan at ${r.upgradeUrl}`);
  lines.push(`  Console: ${r.account.console.replace(/\/+$/, "")}/account`);
  return lines.join("\n");
}
