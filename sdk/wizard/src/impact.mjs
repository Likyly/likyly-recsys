// `npx @likyly/wizard impact [days]`: what the recommendations bring - served, seen, clicked, and
// the purchases and revenue they led to - read from LIKYLY's analytics with the project's
// developer key. A plain API call, no AI involved: the numbers are LIKYLY's, never a guess.
//
// The plan decides what comes back: every plan has the totals and the per-block breakdown
// (purchases and revenue included) over its window (Free: 7 days); Pro adds up to 90 days, the
// day-by-day trend and the products recommendations worked best on.

import { CREDENTIALS_FILE, KEY_ENV } from "./mcp.mjs";
import { readDeveloperKey } from "./cache.mjs";
import { API_URL, wizardUserAgent } from "./verify.mjs";

const isoDay = (d) => d.toISOString().slice(0, 10);
const pct = (n) => `${Math.round(n * 1000) / 10} %`;
const ratio = (a, b) => (b ? a / b : 0);
const oneDecimal = (n) => Math.round(n * 10) / 10;

/**
 * What each number counts - the units differ, which made "28 served, 32 seen" look wrong:
 * served counts blocks (one recommend call per page showing the block), seen counts products
 * (each product of a block that comes into view: a block of 4 seen once is 4).
 */
export const MEANING = {
  served: "blocks served: one per page that asked LIKYLY for the block",
  seen: "products seen: each product of a block that came into view (a block of 4 seen once = 4, a block served but never scrolled to = 0)",
  clicks: "products clicked in a block",
  ctr: "clicks ÷ products seen",
  purchases: "purchases of a product clicked in a block",
};

/** What Pro adds, said once on a plan without it. */
export const PRO_EXTRAS = ["up to 90 days of history", "the day-by-day trend", "the products recommendations worked best on"];

/**
 * Returns { ok: true, report } or { ok: false, text, hint } - never throws.
 * report: { plan, planName, window: {start, end, days, maxDays}, totals, placements[], trend?, topItems?, proExtras?, upgradeUrl }
 */
export async function readImpact({ dir, days, fetchImpl = fetch, now = new Date() }) {
  const key = readDeveloperKey(dir);
  if (!key) {
    return { ok: false, text: `No developer key: ${CREDENTIALS_FILE} is missing and ${KEY_ENV} is not set.`, hint: "Run npx @likyly/wizard in this project first, or export LIKYLY_DEVELOPER_KEY=lk_..." };
  }
  const get = async (path) => {
    const res = await fetchImpl(`${API_URL}${path}`, { headers: { "X-API-Key": key, "User-Agent": wizardUserAgent("impact") }, signal: AbortSignal.timeout(15000) });
    return { status: res.status, ok: res.ok, data: await res.json().catch(() => ({})) };
  };
  try {
    const plan = await get("/analytics/plan");
    if (plan.status === 401 || plan.status === 403)
      return { ok: false, text: "This key cannot read LIKYLY's analytics (it needs the analytics:read right).", hint: "Run npx @likyly/wizard again to get a developer key." };
    if (!plan.ok) return { ok: false, text: `LIKYLY answered ${plan.status}: ${plan.data.detail ?? "unexpected error"}` };

    const limits = plan.data.limits ?? {};
    const maxDays = limits.analytics_window_days ?? null;
    const detail = limits.analytics_detail === true;
    const wanted = Number(days) > 0 ? Math.floor(Number(days)) : detail ? 30 : maxDays ?? 7;
    const span = maxDays ? Math.min(wanted, maxDays) : wanted;
    const end = isoDay(now);
    const start = isoDay(new Date(now.getTime() - (span - 1) * 86400000));
    const q = `?start=${start}&end=${end}`;

    const [summary, byPlacement, trend, items] = await Promise.all([
      get(`/analytics/summary${q}`),
      get(`/analytics/placements${q}`),
      detail ? get(`/analytics/detail${q}`) : null,
      detail ? get(`/analytics/items${q}&limit=5`) : null,
    ]);
    if (!summary.ok) return { ok: false, text: `LIKYLY answered ${summary.status}: ${summary.data.detail ?? "unexpected error"}` };

    const placements = (byPlacement.ok ? byPlacement.data.placements ?? [] : []).map((p) => ({
      placement: p.placement,
      served: p.recommendations_served,
      impressions: p.impressions,
      clicks: p.clicks,
      ctr: p.ctr,
      purchases: p.purchases,
      revenue: p.revenue,
      seenPerServed: oneDecimal(ratio(p.impressions, p.recommendations_served)),
    }));
    const purchases = placements.reduce((n, p) => n + (p.purchases ?? 0), 0);
    const revenue = placements.reduce((n, p) => n + (p.revenue ?? 0), 0);
    const s = summary.data;
    const report = {
      plan: plan.data.plan,
      planName: plan.data.plan_name,
      window: { start: s.start ?? start, end: s.end ?? end, days: s.window_days ?? span, maxDays, asked: wanted },
      totals: {
        served: s.recommendations_served,
        impressions: s.impressions,
        clicks: s.clicks,
        ctr: s.ctr,
        purchases,
        revenue: trend?.ok ? trend.data.revenue?.revenue ?? revenue : revenue,
        clickToPurchase: ratio(purchases, s.clicks),
        seenPerServed: oneDecimal(ratio(s.impressions, s.recommendations_served)),
      },
      meaning: MEANING,
      placements,
      upgradeUrl: plan.data.upgrade_url,
    };
    if (detail) {
      report.trend = trend?.ok ? trend.data.timeseries ?? [] : [];
      report.topItems = items?.ok ? (items.data.items ?? []).map(({ item_id, title, recommended, impressions, clicks, ctr, purchases: bought, revenue: earned }) => ({ itemId: item_id, title, recommended, impressions, clicks, ctr, purchases: bought, revenue: earned })) : [];
    } else {
      report.proExtras = PRO_EXTRAS;
    }
    report.signals = signals(report);
    return { ok: true, report };
  } catch {
    return { ok: false, text: `Could not reach the LIKYLY API (${API_URL}).`, hint: "Check your connection and try again." };
  }
}

/** What the numbers say on their own, before any reading: a block served but never seen, etc. */
function signals({ totals, placements }) {
  const out = [];
  if (!totals.served) out.push({ level: "info", text: "No recommendation served in this window: no page has asked LIKYLY yet." });
  for (const p of placements) {
    if (p.served && !p.impressions) out.push({ level: "fail", text: `${p.placement}: served ${p.served} time(s) but never seen - the page does not send impressions (or LIKYLY refuses them).` });
    else if (p.impressions && !p.clicks) out.push({ level: "info", text: `${p.placement}: seen but not clicked yet.` });
  }
  if (totals.clicks && !totals.purchases) out.push({ level: "info", text: "No purchase attributed yet: purchases count when the site sends them with the recommendationId they came from." });
  return out;
}

/** The terminal version (the /likyly-impact command uses --json and writes its own report). */
export function formatImpact(r) {
  const t = r.totals;
  const lines = [
    `LIKYLY · Impact · ${r.window.start} → ${r.window.end} (${r.window.days} day${r.window.days === 1 ? "" : "s"}, ${r.planName})`,
    `  Blocks served           ${t.served}   (one per page that asked for a block)`,
    `  Products seen           ${t.impressions}   (each product of a block that came into view)`,
    `                               a block of 4 seen = 4, a block never scrolled to = 0${t.served ? ` · here ${t.seenPerServed} per block served` : ""}`,
    `  Products clicked        ${t.clicks} · CTR ${pct(t.ctr)} of the products seen`,
    `  Purchases they led to   ${t.purchases}${t.purchases ? ` · ${t.revenue}` : ""}`,
  ];
  if (r.placements.length) {
    lines.push("", "  By block");
    for (const p of r.placements) lines.push(`    ${p.placement}: ${p.served} served · ${p.impressions} products seen · ${p.clicks} clicked · CTR ${pct(p.ctr)} · ${p.purchases} purchase(s)${p.purchases ? ` · ${p.revenue}` : ""}`);
  }
  for (const s of r.signals) lines.push(`  ${s.level === "fail" ? "✗" : "·"} ${s.text}`);
  if (r.window.asked > r.window.days) lines.push(`  · ${r.window.asked} days asked: the ${r.planName} plan shows ${r.window.days}.`);
  if (r.proExtras) lines.push("", `  On Pro: ${r.proExtras.join(", ")} - ${r.upgradeUrl}`);
  return lines.join("\n");
}
