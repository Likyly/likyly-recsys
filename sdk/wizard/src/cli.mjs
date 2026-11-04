// `npx @likyly/wizard` - detect the framework, install the SDK, write the public key, connect
// the developer's AI coding tool to the LIKYLY MCP server, and hand over to it with a prompt.
//
// In a terminal it runs the Ink UI (src/ui); with --yes, in CI or through a pipe it prints
// plain lines (src/plain.mjs). Both run the same steps (src/actions.mjs).

import { readFileSync } from "node:fs";
import { detectEditors, detectProject } from "./frameworks.mjs";
import { HELP, parseArgs } from "./args.mjs";
import { runPlain } from "./plain.mjs";

export { parseArgs };

export async function main(argv = process.argv.slice(2)) {
  let opts;
  try {
    opts = parseArgs(argv);
  } catch (err) {
    console.error(`Error: ${err.message}\n${HELP}`);
    return 2;
  }
  if (opts.command === "help") {
    console.log(HELP);
    return 0;
  }
  const version = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).version;
  if (opts.command === "version") {
    console.log(version);
    return 0;
  }
  if (opts.command === "cache-clear") {
    const { clearCache } = await import("./cache.mjs");
    const r = await clearCache(opts);
    const color = !process.env.NO_COLOR && process.stdout.isTTY;
    const paint = (code, s) => (color ? `\x1b[${code}m${s}\x1b[0m` : s);
    console.log(`${r.ok ? paint("32", "✓") : paint("31", "✗")} ${r.text}`);
    if (r.hint) console.log(`  ${paint("2", r.hint)}`);
    return r.ok ? 0 : 1;
  }
  if (opts.command === "uninstall") return uninstall(opts);
  if (opts.command === "detect") {
    console.log(JSON.stringify({ project: detectProject(opts.dir), editors: detectEditors(opts.dir) }, null, 2));
    return 0;
  }
  const interactive = !opts.yes && process.stdin.isTTY && process.stdout.isTTY && !process.env.CI;
  if (!interactive) return runPlain(opts);
  const { runInteractive } = await import("./ui/app.jsx");
  return runInteractive(opts, version);
}

async function uninstall(opts) {
  const { planUninstall, runUninstall } = await import("./uninstall.mjs");
  const color = !process.env.NO_COLOR && process.stdout.isTTY;
  const paint = (code, t) => (color ? `\x1b[${code}m${t}\x1b[0m` : t);
  const plan = planUninstall(opts.dir);
  console.log(`\n${paint("1", "LIKYLY uninstall")} ${paint("2", opts.dir)}`);
  if (!plan.steps.length) console.log("  Nothing the wizard added is left in this project.");
  else {
    console.log(paint("1", "\nWill remove"));
    for (const s of plan.steps) console.log(`  - ${s.label}`);
  }
  if (plan.kept.length) {
    console.log(paint("1", "\nKept"));
    for (const k of plan.kept) console.log(`  - ${k}`);
  }
  if (plan.code.length) {
    console.log(paint("1", "\nYour code still uses LIKYLY (not edited)"));
    for (const f of plan.code) console.log(`  - ${f}`);
    console.log(paint("2", '  Ask your assistant: "Remove LIKYLY from my code and restore what these pages showed before."'));
  }
  console.log(paint("2", "\nYour LIKYLY account (catalog, placements, keys) is not touched: https://www.likyly.com/account"));
  if (!plan.steps.length || opts.dryRun) {
    if (opts.dryRun && plan.steps.length) console.log(paint("33", "\nDry run: nothing was removed. Run it again without --dry-run (or with --yes) to proceed."));
    return 0;
  }
  if (!opts.yes) {
    if (!process.stdin.isTTY) {
      console.log(paint("33", "\nNot a terminal: add --yes to proceed."));
      return 1;
    }
    const { createInterface } = await import("node:readline/promises");
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    const answer = (await rl.question("\nProceed? [y/N] ")).trim().toLowerCase();
    rl.close();
    if (!["y", "yes", "o", "oui"].includes(answer)) {
      console.log("Cancelled, nothing was removed.");
      return 0;
    }
  }
  console.log("");
  const results = await runUninstall(opts.dir, plan, (l) => console.log(paint("2", `    ${l}`)));
  for (const r of results) console.log(`  ${r.ok ? paint("32", "✓") : paint("31", "✗")} ${r.label}`);
  return results.every((r) => r.ok) ? 0 : 1;
}
