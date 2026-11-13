// Line-by-line renderer for CI and non-TTY runs (`--yes`, pipes): no prompts, stable output,
// non-zero exit when a step fails.

import { detectEditors, detectProject } from "./frameworks.mjs";
import { KEY_ENV } from "./mcp.mjs";
import { applyKey, applyStarter, connectEditors, installSdk } from "./actions.mjs";
import { AGENT_PROMPT, DOCS_URL, KEYS_URL, TAGLINE } from "./constants.mjs";

const color = !process.env.NO_COLOR && process.stdout.isTTY;
const paint = (code) => (s) => (color ? `\x1b[${code}m${s}\x1b[0m` : String(s));
const bold = paint("1");
const dim = paint("2");
const mark = { ok: paint("32")("✓"), info: dim("·"), warn: paint("33")("! To do:"), fail: paint("31")("✗ Error:") };

function report(results) {
  let failed = 0;
  for (const r of results) {
    if (r.level === "fail") failed++;
    console.log(`  ${mark[r.level]} ${r.text}${r.hint ? ` ${bold(r.hint)}` : ""}`);
  }
  return failed;
}

const step = (n, total, title) => console.log(`\n${dim(`[${n}/${total}]`)} ${bold(title)}`);

function chosenEditors(opts) {
  if (opts.editors) return opts.editors;
  const detected = detectEditors(opts.dir);
  return detected.length ? detected : ["claude-code"];
}

function handOff(editors) {
  console.log(`\n${bold("Last step: let your AI assistant finish the job")}`);
  if (editors.length) {
    console.log(`  1. Create a ${bold("developer key")} at ${KEYS_URL} and export it:`);
    console.log(`     ${dim("$")} export ${KEY_ENV}=lk_...`);
    console.log(`  2. Restart your assistant and paste this prompt:`);
  } else console.log("  Paste this prompt into your AI assistant:");
  console.log(`\n     "${AGENT_PROMPT}"\n\n  Docs: ${DOCS_URL}\n`);
}

export async function runPlain(opts) {
  console.log(`\n${bold("LIKYLY")} ${dim(`· ${TAGLINE}`)}`);
  if (opts.dryRun) console.log(paint("33")("  Dry run: nothing will be written or installed."));
  let failed = 0;
  const editors = chosenEditors(opts);

  if (opts.command === "mcp") {
    failed += report(connectEditors(opts, editors));
    handOff(editors);
    return failed ? 1 : 0;
  }

  const project = detectProject(opts.dir);
  step(1, 4, "Detect the project");
  if (!project) {
    report([{ level: "warn", text: "No project recognised here (no package.json, composer.json, pyproject.toml, Gemfile, go.mod, Cargo.toml, .csproj, pom.xml, WordPress or Shopify theme). Run it at your project root, or follow", hint: DOCS_URL }]);
    return 1;
  }
  report([{ level: "ok", text: `${project.label}${project.packageManager ? ` (${project.packageManager})` : ""}` }]);

  step(2, 4, "Install the SDK");
  failed += report(await installSdk(opts, project, (l) => console.log(dim(`    ${l}`))));

  step(3, 4, "Public key and starter file");
  failed += report([...applyKey(opts, project, opts.publicKey), ...applyStarter(opts, project)]);

  step(4, 4, "Connect your AI assistant (MCP)");
  failed += report(connectEditors(opts, editors));

  handOff(editors);
  return failed ? 1 : 0;
}
