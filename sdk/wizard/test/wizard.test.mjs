import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { detectProject, detectEditors, installedEditors, EDITORS } from "../src/frameworks.mjs";
import { mergeConfig, writeMcpConfig, removeTomlBlock, approveClaudeServer, hasDeveloperKey } from "../src/mcp.mjs";
import { isServerOnlyKey, writeEnv, writeStarter } from "../src/setup.mjs";
import { parseArgs } from "../src/cli.mjs";
import { describePlan, installMode } from "../src/actions.mjs";

const BIN = fileURLToPath(new URL("../bin/likyly-wizard.mjs", import.meta.url));

function project(files) {
  const dir = mkdtempSync(join(tmpdir(), "likyly-wizard-"));
  for (const [name, content] of Object.entries(files)) {
    const path = join(dir, name);
    mkdirSync(join(path, ".."), { recursive: true });
    if (name.endsWith("/")) mkdirSync(path, { recursive: true });
    else writeFileSync(path, typeof content === "string" ? content : JSON.stringify(content));
  }
  return dir;
}

const pkg = (deps) => ({ "package.json": { dependencies: deps } });

test("detects Next.js (app router, pnpm) with the React SDK", () => {
  const dir = project({ ...pkg({ next: "16", react: "19" }), "pnpm-lock.yaml": "", "src/app/": "" });
  const p = detectProject(dir);
  assert.equal(p.id, "nextjs");
  assert.deepEqual(p.install.cmd, ["pnpm", "add", "@likyly/sdk", "@likyly/react"]);
  assert.equal(p.envVar, "NEXT_PUBLIC_LIKYLY_KEY");
  assert.equal(p.starter.path, "src/components/likyly-provider.tsx");
  assert.equal(p.starter.template, "next-app");
});

test("meta-frameworks win over the library they are built on", () => {
  assert.equal(detectProject(project(pkg({ nuxt: "3", vue: "3" }))).id, "nuxt");
  assert.equal(detectProject(project(pkg({ "@sveltejs/kit": "2", svelte: "5" }))).id, "sveltekit");
  assert.equal(detectProject(project(pkg({ react: "19", vite: "6" }))).envVar, "VITE_LIKYLY_KEY");
  assert.equal(detectProject(project({ ...pkg({ express: "5" }), "yarn.lock": "" })).install.cmd[0], "yarn");
});

test("detects PHP, Python, Ruby, Go and WordPress stacks", () => {
  assert.equal(detectProject(project({ "composer.json": { require: { "laravel/framework": "^11" } } })).id, "laravel");
  assert.equal(detectProject(project({ "composer.json": { require: { "symfony/framework-bundle": "^7" } } })).envFile, ".env.local");
  // Laravel ships a package.json for Vite: still Laravel, with the PHP SDK.
  const laravel = detectProject(project({ "composer.json": { require: { "laravel/framework": "^13" } }, ...pkg({ vite: "8", "laravel-vite-plugin": "3" }) }));
  assert.equal(laravel.id, "laravel");
  assert.deepEqual(laravel.packages, ["likyly/laravel"]);
  assert.deepEqual(laravel.install.cmd, ["composer", "require", "likyly/laravel"]);
  assert.equal(laravel.next, "php artisan likyly:install");
  assert.equal(detectProject(project({ "composer.json": { require: { "symfony/framework-bundle": "^8" } }, ...pkg({ "@symfony/webpack-encore": "5" }) })).id, "symfony");
  assert.equal(detectProject(project({ "composer.json": { require: { "drupal/core-recommended": "^11" } } })).label, "Drupal");
  // A plain composer.json next to a JS framework stays a JS project.
  assert.equal(detectProject(project({ "composer.json": { require: { "guzzlehttp/guzzle": "^7" } }, ...pkg({ next: "16", react: "19" }) })).id, "nextjs");
  const django = detectProject(project({ "manage.py": "", "requirements.txt": "Django==5" }));
  assert.equal(django.id, "django");
  assert.equal(django.install.run, false, "bare pip is printed, not run");
  assert.deepEqual(detectProject(project({ "pyproject.toml": "fastapi", "uv.lock": "" })).install.cmd, ["uv", "add", "likyly"]);
  assert.equal(detectProject(project({ Gemfile: "gem 'rails'" })).id, "rails");
  assert.equal(detectProject(project({ "go.mod": "module x" })).id, "go");
  const wp = detectProject(project({ "wp-config.php": "", ...pkg({ react: "19" }) }));
  assert.equal(wp.id, "wordpress");
  assert.equal(wp.install, null);
});

test("returns null for an empty folder", () => {
  assert.equal(detectProject(project({})), null);
});

test("detects AI editors from their project folders", () => {
  assert.deepEqual(detectEditors(project({ ".cursor/": "", "CLAUDE.md": "" })), ["claude-code", "cursor"]);
});

test("MCP config keeps other servers and never contains a literal key", () => {
  const existing = { mcpServers: { other: { command: "x" } } };
  const { config, replaced } = mergeConfig("claude-code", existing, {});
  assert.equal(replaced, false);
  assert.deepEqual(config.mcpServers.other, { command: "x" });
  assert.equal(config.mcpServers.likyly.env.LIKYLY_API_KEY, "${LIKYLY_DEVELOPER_KEY:-}");
  const cursor = mergeConfig("cursor", null, {}).config.mcpServers.likyly.env;
  assert.equal(cursor.LIKYLY_API_KEY, "${env:LIKYLY_DEVELOPER_KEY}");
  assert.equal(cursor.LIKYLY_ENV_FILE, "${workspaceFolder}/.likyly/credentials.env");

  const vs = mergeConfig("vscode", { inputs: [{ id: "keep" }, { id: "likyly-developer-key" }] }, {}).config;
  assert.equal(vs.servers.likyly.type, "stdio");
  assert.deepEqual(vs.inputs.map((i) => i.id), ["keep"], "the old key prompt is dropped");

  const hosted = mergeConfig("claude-code", null, { hosted: true }).config.mcpServers.likyly;
  assert.equal(hosted.type, "http");
  assert.equal(hosted.url, "https://mcp.likyly.com/mcp");
});

test("writeMcpConfig refuses to clobber an unparseable file", () => {
  const dir = project({ ".mcp.json": "{ // comment\n}" });
  assert.throws(() => writeMcpConfig(dir, "claude-code"), /not valid JSON/);
});

test("server-only keys are recognised", () => {
  assert.equal(isServerOnlyKey("sk_live_123"), true);
  assert.equal(isServerOnlyKey(" lk_dev"), true);
  assert.equal(isServerOnlyKey("pk_abc"), false);
});

test("env and starter writes are additive", () => {
  const dir = project({ ".env.local": "OTHER=1" });
  assert.equal(writeEnv(dir, ".env.local", "NEXT_PUBLIC_LIKYLY_KEY", "pk_1").status, "added");
  assert.equal(writeEnv(dir, ".env.local", "NEXT_PUBLIC_LIKYLY_KEY", "pk_1").status, "same");
  const upd = writeEnv(dir, ".env.local", "NEXT_PUBLIC_LIKYLY_KEY", "pk_2");
  assert.deepEqual([upd.status, upd.previous], ["updated", "pk_1"], "a regenerated key replaces the dead one");
  const env = readFileSync(join(dir, ".env.local"), "utf8");
  assert.match(env, /^OTHER=1\n/);
  assert.match(env, /NEXT_PUBLIC_LIKYLY_KEY=pk_2\n$/);
  assert.equal(env.match(/NEXT_PUBLIC_LIKYLY_KEY/g).length, 1);

  const starter = { path: "src/likyly.ts", template: "vanilla", envExpr: "import.meta.env.VITE_LIKYLY_KEY" };
  assert.equal(writeStarter(dir, starter).status, "created");
  assert.match(readFileSync(join(dir, "src/likyly.ts"), "utf8"), /apiKey: import\.meta\.env\.VITE_LIKYLY_KEY/);
  assert.equal(writeStarter(dir, starter).status, "exists");
});

test("argument parsing", () => {
  assert.equal(parseArgs(["mcp", "add"]).command, "mcp");
  assert.deepEqual(parseArgs(["--editor", "cursor,vscode"]).editors, ["cursor", "vscode"]);
  assert.throws(() => parseArgs(["--editor", "emacs"]), /unknown editor/);
  assert.throws(() => parseArgs(["--nope"]), /unknown option/);
  assert.throws(() => parseArgs(["--public-key", " sk_live_x"]), /secret or developer key/);
  assert.equal(parseArgs(["--public-key", "pk_live_x"]).publicKey, "pk_live_x");
});

function runCli(args, cwd) {
  return spawnSync(process.execPath, [BIN, ...args], { cwd, encoding: "utf8", env: { ...process.env, NO_COLOR: "1" } });
}

test("end to end: --dry-run writes nothing", () => {
  const dir = project({ ...pkg({ next: "16", react: "19" }), "app/": "" });
  const res = runCli(["--yes", "--dry-run", "--public-key", "pk_test", "--dir", dir], dir);
  assert.equal(res.status, 0, res.stderr);
  assert.match(res.stdout, /✓ Next\.js/);
  assert.equal(existsSync(join(dir, ".env.local")), false);
  assert.equal(existsSync(join(dir, ".mcp.json")), false);
});

test("end to end: --yes --no-install sets up a Next.js project", () => {
  const dir = project({ ...pkg({ next: "16", react: "19" }), "app/": "", ".cursor/": "" });
  const res = runCli(["--yes", "--no-install", "--public-key", "pk_test", "--dir", dir], dir);
  assert.equal(res.status, 0, res.stderr);
  assert.match(readFileSync(join(dir, ".env.local"), "utf8"), /NEXT_PUBLIC_LIKYLY_KEY=pk_test/);
  assert.ok(existsSync(join(dir, "components/likyly-provider.tsx")));
  assert.ok(existsSync(join(dir, ".cursor/mcp.json")));
  assert.equal(existsSync(join(dir, ".mcp.json")), false, "only the detected editor is connected");
});

test("end to end: a secret key is refused before anything is written", () => {
  for (const key of ["sk_live_x", "lk_dev_x"]) {
    const dir = project({ ...pkg({ react: "19", vite: "6" }) });
    const res = runCli(["--yes", "--no-install", "--public-key", key, "--dir", dir], dir);
    assert.equal(res.status, 2, "exits with an error so CI notices it");
    assert.match(res.stderr, /secret or developer key/);
    assert.equal(existsSync(join(dir, ".env.local")), false);
    assert.equal(existsSync(join(dir, ".mcp.json")), false, "no step runs");
    assert.equal(existsSync(join(dir, "src/likyly.ts")), false, "no step runs");
  }
});

test("end to end: mcp add for Claude Code installs the skill", () => {
  const dir = project({});
  const res = runCli(["mcp", "add", "--yes", "--editor", "claude-code", "--dir", dir], dir);
  assert.equal(res.status, 0, res.stderr);
  assert.ok(existsSync(join(dir, ".mcp.json")));
  assert.match(readFileSync(join(dir, ".claude/skills/likyly/SKILL.md"), "utf8"), /name: likyly/);
  // Run from this checkout (not from npm): the commands call this very file.
  assert.match(readFileSync(join(dir, ".claude/commands/likyly-clear-cache.md"), "utf8"), /node \S+likyly-wizard\.mjs cache clear \$ARGUMENTS/);
  assert.match(readFileSync(join(dir, ".claude/commands/likyly-uninstall.md"), "utf8"), /uninstall --yes --branch new/);
  assert.match(readFileSync(join(dir, ".claude/commands/likyly-help.md"), "utf8"), /\/likyly-clear-cache \[placement\]/);
  const help = readFileSync(join(dir, ".claude/commands/likyly-help.md"), "utf8");
  assert.match(help, /\/likyly-inventory/);
  assert.match(help, /\/likyly-model \[catalog\] \[days\]/);
  const inventory = readFileSync(join(dir, ".claude/commands/likyly-inventory.md"), "utf8");
  assert.match(inventory, /likyly-wizard\.mjs inventory`/);
  assert.match(inventory, /mcp__likyly__list_placements/);
  const model = readFileSync(join(dir, ".claude/commands/likyly-model.md"), "utf8");
  assert.match(model, /mcp__likyly__get_model_overview/);
  for (const text of [inventory, model]) assert.doesNotMatch(text, /LIKYLY_DEVELOPER_KEY/, "never reads the key line");
});

test("inventory lists what LIKYLY put in a project, never a key value", () => {
  const dir = project({
    "package.json": { dependencies: { next: "15.0.0", react: "19.0.0", "@likyly/react": "^0.2.0", "@likyly/sdk": "^1.1.0" } },
    ".env.local": "OTHER=1\nNEXT_PUBLIC_LIKYLY_KEY=pk_secretvalue123\n",
    ".mcp.json": { mcpServers: { likyly: { command: "npx" } } },
    ".likyly/credentials.env": "# Account: dev@example.com\n# Workspace: Shop\n# Console: https://www.likyly.com\nLIKYLY_DEVELOPER_KEY=lk_topsecret\n",
    "src/app/products/[id]/page.tsx": 'import { LikylyRecommendations } from "@likyly/react";\nexport default function P() {\n  return <LikylyRecommendations placement="pdp-related" />;\n}\n',
    "src/app/page.tsx": "export default function Home() { return null; }\n",
    "node_modules/@likyly/react/package.json": { name: "@likyly/react", version: "0.2.0" },
  });
  const res = runCli(["inventory", "--json", "--dir", dir], dir);
  assert.equal(res.status, 0, res.stderr);
  const inv = JSON.parse(res.stdout);
  assert.deepEqual(inv.packages.map((p) => [p.name, p.version]), [["@likyly/react", "0.2.0"], ["@likyly/sdk", null]]);
  assert.deepEqual(inv.keys, [{ file: ".env.local", variable: "NEXT_PUBLIC_LIKYLY_KEY" }]);
  assert.deepEqual(inv.account, { account: "dev@example.com", workspace: "Shop", console: "https://www.likyly.com" });
  assert.ok(inv.setup.some((s) => s.what === "mcp" && s.path === ".mcp.json"));
  assert.deepEqual(inv.code.map((f) => f.file), ["src/app/products/[id]/page.tsx"], "files without LIKYLY are left out");
  assert.deepEqual(inv.code[0].lines.map((l) => l.line), [1, 3]);

  const text = runCli(["inventory", "--dir", dir], dir);
  assert.equal(text.status, 0, text.stderr);
  assert.match(text.stdout, /NEXT_PUBLIC_LIKYLY_KEY in \.env\.local/);
  for (const out of [res.stdout, text.stdout]) {
    assert.doesNotMatch(out, /pk_secretvalue123|lk_topsecret/, "key values are never printed");
  }
});

test("commands call npx from the npm package, the file itself from a checkout", async () => {
  const { wizardInvocation } = await import("../src/setup.mjs");
  assert.equal(wizardInvocation("/Users/me/.npm/_npx/abc/node_modules/@likyly/wizard/bin/likyly-wizard.mjs"), "npx -y @likyly/wizard");
  assert.equal(wizardInvocation("/repo/sdk/wizard/bin/likyly-wizard.mjs"), "node /repo/sdk/wizard/bin/likyly-wizard.mjs");
  assert.equal(wizardInvocation(undefined), "npx -y @likyly/wizard");
});

test("the plan lists what will be run, written and connected", () => {
  const dir = project({ ...pkg({ next: "16", react: "19" }), "app/": "" });
  const p = detectProject(dir);
  const opts = { dir, install: false, dryRun: false };
  assert.equal(installMode(opts, p), "manual");
  const rows = describePlan(opts, p);
  assert.deepEqual(rows[0], ["Show", "npm install @likyly/sdk @likyly/react"]);
  assert.deepEqual(rows[1], ["Key", "NEXT_PUBLIC_LIKYLY_KEY → .env.local"]);
  assert.deepEqual(rows[2], ["Create", "components/likyly-provider.tsx"]);
});

test("everything the developer reads is in English", () => {
  const dir = project({ ...pkg({ next: "16", react: "19" }), "app/": "" });
  const res = runCli(["--yes", "--no-install", "--public-key", "pk_test", "--dir", dir], dir);
  const written = ["components/likyly-provider.tsx", ".env.local"].map((f) => readFileSync(join(dir, f), "utf8")).join("\n");
  for (const text of [res.stdout, written]) assert.doesNotMatch(text, /[éèàçù]|Généré|clé/);
});

test("compatibility reads installed versions first, then declared ranges", async () => {
  const { checkCompat } = await import("../src/compat.mjs");
  const dir = project({ ...pkg({ next: "^16.0.1", react: "^19.0.0" }), "node_modules/react/package.json": { version: "19.2.0" } });
  const c = checkCompat(dir, detectProject(dir));
  assert.deepEqual(c.items.slice(0, 2).map((i) => [i.label, i.version, i.ok]), [["Next.js", "16.0.1", true], ["React", "19.2.0", true]]);
  assert.equal(c.ok, true);
  const old = project(pkg({ next: "12.3.0", react: "17.0.2" }));
  const o = checkCompat(old, detectProject(old));
  assert.equal(o.ok, false);
  assert.deepEqual(o.items.filter((i) => !i.ok).map((i) => i.label), ["Next.js", "React"]);
});

test("the developer key is stored outside git, readable by its owner only", async () => {
  const { writeCredentials } = await import("../src/setup.mjs");
  const { statSync } = await import("node:fs");
  const dir = project({});
  const { path } = writeCredentials(dir, "lk_test_123");
  assert.match(readFileSync(path, "utf8"), /^LIKYLY_DEVELOPER_KEY=lk_test_123$/m);
  assert.equal(readFileSync(join(dir, ".likyly/.gitignore"), "utf8").trim().split("\n").pop(), "*");
  assert.equal(statSync(path).mode & 0o777, 0o600);
});

test("browser sign-in: state is checked, keys come back, server-only keys are refused", async () => {
  const { startBrowserAuth } = await import("../src/auth.mjs");
  const auth = await startBrowserAuth({ project: { label: "Next.js" }, dir: "/tmp/shop" });
  const url = new URL(auth.url);
  assert.equal(url.pathname, "/cli/auth");
  assert.equal(url.searchParams.get("project"), "shop");
  const post = (fields) => fetch(`http://127.0.0.1:${url.searchParams.get("port")}/callback`, { method: "POST", body: new URLSearchParams(fields) });
  assert.equal((await post({ state: "nope", key: "pk_1" })).status, 400);
  assert.equal((await post({ state: url.searchParams.get("state"), key: "sk_live_1" })).status, 400);
  try {
    assert.equal((await post({ state: url.searchParams.get("state"), key: "pk_live_1", dev_key: "lk_dev_1", workspace: "Shop", account: "dev@shop.test" })).status, 200);
    assert.deepEqual(await auth.result, { key: "pk_live_1", devKey: "lk_dev_1", workspace: "Shop", account: "dev@shop.test" });
  } finally {
    auth.close();
  }
});

test("cache clear: parsing, and a clear message without a developer key", async () => {
  assert.deepEqual([parseArgs(["cache", "clear"]).command, parseArgs(["cache", "clear"]).placement], ["cache-clear", null]);
  assert.equal(parseArgs(["cache", "purge", "pdp-related"]).placement, "pdp-related");
  assert.equal(parseArgs(["cache", "clear", "--placement", "home"]).placement, "home");
  assert.throws(() => parseArgs(["cache", "flush"]), /cache clear/);
  const dir = project({});
  const res = spawnSync(process.execPath, [BIN, "cache", "clear", "--dir", dir], { encoding: "utf8", env: { ...process.env, NO_COLOR: "1", LIKYLY_DEVELOPER_KEY: "" } });
  assert.equal(res.status, 1);
  assert.match(res.stdout, /No developer key/);
});

test("cache clear reads the key from the credentials file and calls the API", async () => {
  const { createServer } = await import("node:http");
  const seen = [];
  const server = createServer((req, res) => {
    seen.push([req.method, req.url, req.headers["x-api-key"]]);
    res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ message: "ok", purged: 3 }));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const dir = project({ ".likyly/credentials.env": "LIKYLY_DEVELOPER_KEY=lk_test_9\n" });
  const res = await new Promise((resolve) => {
    const child = spawn(process.execPath, [BIN, "cache", "clear", "pdp-related", "--dir", dir], {
      env: { ...process.env, NO_COLOR: "1", LIKYLY_DEVELOPER_KEY: "", LIKYLY_API_URL: `http://127.0.0.1:${server.address().port}` },
    });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.on("close", (code) => resolve({ code, out }));
  });
  server.close();
  assert.equal(res.code, 0, res.out);
  assert.deepEqual(seen, [["POST", "/placements/pdp-related/cache/purge", "lk_test_9"]]);
  assert.match(res.out, /3 cached answers dropped/);
});

test("uninstall removes only what the wizard added, after a dry run that removes nothing", () => {
  const dir = project({
    ...pkg({ next: "16", react: "19" }),
    "app/": "",
    ".env.local": "OTHER=1\n",
    ".cursor/mcp.json": { mcpServers: { other: { command: "x" } } },
    "CLAUDE.md": "",
    "src/page.tsx": 'import { LikylyRecommendations } from "@likyly/react";\n',
  });
  assert.equal(runCli(["--yes", "--no-install", "--public-key", "pk_test", "--editor", "claude-code,cursor", "--dir", dir], dir).status, 0);
  mkdirSync(join(dir, ".likyly"), { recursive: true });
  writeFileSync(join(dir, ".likyly/credentials.env"), "LIKYLY_DEVELOPER_KEY=lk_x\n");

  const dry = runCli(["uninstall", "--dry-run", "--dir", dir], dir);
  assert.equal(dry.status, 0, dry.stderr);
  assert.match(dry.stdout, /Remove the LIKYLY key from \.env\.local/);
  assert.match(dry.stdout, /src\/page\.tsx/, "code that uses LIKYLY is listed");
  assert.ok(existsSync(join(dir, ".mcp.json")), "a dry run removes nothing");

  const res = runCli(["uninstall", "--yes", "--dir", dir], dir);
  assert.equal(res.status, 0, res.stdout + res.stderr);
  assert.equal(readFileSync(join(dir, ".env.local"), "utf8").trim(), "OTHER=1");
  assert.equal(existsSync(join(dir, "components/likyly-provider.tsx")), false);
  assert.equal(existsSync(join(dir, ".mcp.json")), false, "only LIKYLY was in it");
  assert.deepEqual(JSON.parse(readFileSync(join(dir, ".cursor/mcp.json"), "utf8")), { mcpServers: { other: { command: "x" } } });
  for (const gone of [".claude/skills/likyly", ".claude/commands/likyly-help.md", ".claude/commands/likyly-inventory.md", ".claude/commands/likyly-model.md", ".claude/commands/likyly-clear-cache.md", ".claude/commands/likyly-uninstall.md", ".likyly"]) {
    assert.equal(existsSync(join(dir, gone)), false, gone);
  }
  assert.equal(readFileSync(join(dir, "src/page.tsx"), "utf8"), 'import { LikylyRecommendations } from "@likyly/react";\n', "code is never edited");
});

test("uninstall keeps a starter file the developer edited", () => {
  const dir = project({ ...pkg({ next: "16", react: "19" }), "app/": "" });
  runCli(["--yes", "--no-install", "--public-key", "pk_test", "--editor", "claude-code", "--dir", dir], dir);
  writeFileSync(join(dir, "components/likyly-provider.tsx"), "// mine now\n");
  const res = runCli(["uninstall", "--yes", "--dir", dir], dir);
  assert.match(res.stdout, /edited since the wizard wrote it/);
  assert.ok(existsSync(join(dir, "components/likyly-provider.tsx")));
});

test("review: only what changed after the snapshot is listed, on the likyly/setup branch", async () => {
  const git = await import("../src/git.mjs");
  const dir = project({ "package.json": { name: "x" }, "app/page.tsx": "a\n", ".gitignore": ".env*.local\n" });
  const run = (...args) => assert.equal(spawnSync("git", args, { cwd: dir }).status, 0, args.join(" "));
  run("init", "-q");
  run("add", "-A");
  run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init");
  writeFileSync(join(dir, "app/page.tsx"), "a\nmine\n"); // the developer's pending work
  const before = git.snapshot(dir);
  assert.ok(git.createBranch(dir).ok);
  assert.equal(git.currentBranch(dir), "likyly/setup");
  writeFileSync(join(dir, "package.json"), '{"name":"x","dependencies":{"@likyly/sdk":"1"}}');
  writeFileSync(join(dir, "likyly.ts"), "new\n");
  writeFileSync(join(dir, ".env.local"), "K=1\n");
  const { mine, alreadyModified } = git.changesSince(dir, before);
  assert.deepEqual(mine.map((f) => `${f.code} ${f.path}`).sort(), ["A likyly.ts", "M package.json"]);
  assert.equal(alreadyModified, 1, "the developer's own change is counted, not listed");
  assert.deepEqual(git.ignored(dir, [".env.local"]), [".env.local"]);
  assert.equal(readFileSync(join(dir, "app/page.tsx"), "utf8"), "a\nmine\n", "pending work untouched");
});

test("mcp: LIKYLY_API_URL and LIKYLY_MCP_SERVER give a local server config", async () => {
  const { serverEntry } = await import("../src/mcp.mjs");
  process.env.LIKYLY_API_URL = "http://127.0.0.1:6061/";
  process.env.LIKYLY_MCP_SERVER = "/abs/sdk/mcp/dist/index.js";
  try {
    const e = serverEntry("claude-code");
    assert.equal(e.command, "node");
    assert.deepEqual(e.args, ["/abs/sdk/mcp/dist/index.js"]);
    assert.equal(e.env.LIKYLY_BASE_URL, "http://127.0.0.1:6061");
  } finally {
    delete process.env.LIKYLY_API_URL;
    delete process.env.LIKYLY_MCP_SERVER;
  }
  const prod = serverEntry("claude-code");
  assert.equal(prod.command, "npx");
  assert.equal(prod.env.LIKYLY_BASE_URL, "https://api.likyly.com");
});

test("uninstall --branch new works on likyly/uninstall; --branch current stays", () => {
  const dir = project({ ...pkg({ next: "16", react: "19" }), "app/": "" });
  const g = (...args) => spawnSync("git", args, { cwd: dir, encoding: "utf8" });
  g("init", "-q", "-b", "main");
  g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init");
  assert.equal(runCli(["--yes", "--no-install", "--public-key", "pk_test", "--editor", "claude-code", "--dir", dir], dir).status, 0);
  const res = runCli(["uninstall", "--yes", "--branch", "new", "--dir", dir], dir);
  assert.equal(res.status, 0, res.stdout + res.stderr);
  assert.match(res.stdout, /On a new branch likyly\/uninstall/);
  assert.equal(g("branch", "--show-current").stdout.trim(), "likyly/uninstall");
  assert.equal(existsSync(join(dir, ".mcp.json")), false);

  g("switch", "-q", "main");
  assert.equal(runCli(["--yes", "--no-install", "--public-key", "pk_test", "--editor", "claude-code", "--dir", dir], dir).status, 0);
  const stay = runCli(["uninstall", "--yes", "--branch", "current", "--dir", dir], dir);
  assert.equal(stay.status, 0, stay.stdout + stay.stderr);
  assert.equal(g("branch", "--show-current").stdout.trim(), "main");
});

test("help is a command too", () => {
  assert.equal(parseArgs(["help"]).command, "help");
  assert.throws(() => parseArgs(["uninstall", "--branch", "elsewhere"]), /--branch expects new or current/);
});

const shopifyTheme = () =>
  project({ "layout/theme.liquid": "<html></html>", "config/settings_schema.json": "[]", "sections/main-product.liquid": "", "package.json": { devDependencies: { prettier: "3" } } });

test("detects a Shopify theme, even with a package.json for its tooling", () => {
  const found = detectProject(shopifyTheme());
  assert.equal(found.id, "shopify");
  assert.equal(found.install, null);
  assert.equal(found.starter.path, "sections/likyly-recommendations.liquid");
  assert.equal(detectProject(project({ "layout/theme.liquid": "" }))?.id, undefined, "a lone theme.liquid is not enough");
});

test("end to end: a Shopify theme gets the key snippet and the section, nothing else", () => {
  const dir = shopifyTheme();
  const res = runCli(["--yes", "--public-key", "pk_test_shop", "--dir", dir], dir);
  assert.equal(res.status, 0, res.stderr);
  assert.match(res.stdout, /Shopify theme/);
  assert.match(res.stdout, /shopify theme dev/);
  assert.match(readFileSync(join(dir, "snippets/likyly-key.liquid"), "utf8"), /endcomment -%}pk_test_shop\n$/);
  const section = readFileSync(join(dir, "sections/likyly-recommendations.liquid"), "utf8");
  // The schema is valid JSON, and the module script is valid JavaScript.
  JSON.parse(section.match(/{% schema %}([\s\S]*){% endschema %}/)[1]);
  const script = section.match(/<script type="module">([\s\S]*)<\/script>/)[1].replace(/^\s*import .*$/m, "");
  const check = spawnSync(process.execPath, ["--input-type=module", "--check"], { input: script, encoding: "utf8" });
  assert.equal(check.status, 0, check.stderr);
  assert.doesNotMatch(section, /[éèàçù]/);

  // A new key replaces the old one; the same key leaves the file alone.
  runCli(["--yes", "--public-key", "pk_test_new", "--dir", dir], dir);
  assert.match(readFileSync(join(dir, "snippets/likyly-key.liquid"), "utf8"), /pk_test_new/);
  assert.doesNotMatch(readFileSync(join(dir, "snippets/likyly-key.liquid"), "utf8"), /pk_test_shop/);
});

test("an unrecognised folder says so and exits with an error", () => {
  const dir = project({});
  const res = runCli(["--yes", "--dir", dir], dir);
  assert.equal(res.status, 1);
  assert.match(res.stdout + res.stderr, /No project recognised here .*Shopify theme/);
});


// --- Every supported assistant: Codex, Antigravity, Mistral Vibe, Kiro, Junie ------------------

test("Codex: a TOML block that keeps the rest of the file, updates in place, and comes out cleanly", () => {
  const dir = project({ ".codex/config.toml": 'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "x"\n' });
  const first = writeMcpConfig(dir, "codex");
  assert.equal(first.replaced, false);
  let text = readFileSync(join(dir, ".codex/config.toml"), "utf8");
  assert.match(text, /^model = "gpt-5"/);
  assert.match(text, /\[mcp_servers\.other\]/);
  assert.match(text, /\[mcp_servers\.likyly\]\ncommand = "npx"\nargs = \["-y", "-p", "@likyly\/mcp-server", "likyly-mcp-server"\]\nenv_vars = \["LIKYLY_DEVELOPER_KEY"\]/);
  assert.doesNotMatch(text, /lk_|LIKYLY_API_KEY/, "never a key in the file");

  assert.equal(writeMcpConfig(dir, "codex").replaced, true);
  text = readFileSync(join(dir, ".codex/config.toml"), "utf8");
  assert.equal(text.match(/\[mcp_servers\.likyly\]/g).length, 1, "updated, not duplicated");
  assert.equal(removeTomlBlock(text), 'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "x"\n');
});

test("Codex and Vibe: a likyly server written by hand is left alone", () => {
  const dir = project({ ".codex/config.toml": '[mcp_servers.likyly]\ncommand = "mine"\n', ".vibe/config.toml": '[[mcp_servers]]\nname = "likyly"\n' });
  assert.throws(() => writeMcpConfig(dir, "codex"), /already defines a likyly server/);
  assert.throws(() => writeMcpConfig(dir, "mistral-vibe"), /already defines a likyly server/);
});

test("Mistral Vibe: an [[mcp_servers]] entry, stdio", () => {
  const dir = project({});
  writeMcpConfig(dir, "mistral-vibe");
  const text = readFileSync(join(dir, ".vibe/config.toml"), "utf8");
  assert.match(text, /\[\[mcp_servers\]\]\nname = "likyly"\ntransport = "stdio"\ncommand = "npx"/);
});

test("Antigravity, Kiro, Junie: mcpServers in their own file, no key reference (read from the credentials file)", () => {
  const dir = project({});
  for (const [id, file] of [["antigravity", ".agents/mcp_config.json"], ["kiro", ".kiro/settings/mcp.json"], ["junie", ".junie/mcp/mcp.json"]]) {
    writeMcpConfig(dir, id);
    const entry = JSON.parse(readFileSync(join(dir, file), "utf8")).mcpServers.likyly;
    assert.equal(entry.command, "npx", id);
    assert.deepEqual(Object.keys(entry.env), ["LIKYLY_BASE_URL"], id);
  }
});

test("the original three keep their exact config", () => {
  assert.equal(mergeConfig("claude-code", null).config.mcpServers.likyly.env.LIKYLY_API_KEY, "${LIKYLY_DEVELOPER_KEY:-}");
  assert.equal(mergeConfig("cursor", null).config.mcpServers.likyly.env.LIKYLY_ENV_FILE, "${workspaceFolder}/.likyly/credentials.env");
  assert.equal(mergeConfig("vscode", null).config.servers.likyly.type, "stdio");
});

test("end to end: every assistant gets its config, and the skill once per folder it reads", () => {
  const dir = project({ ...pkg({ next: "16", react: "19" }), "app/": "" });
  const all = EDITORS.map((e) => e.id).join(",");
  const res = runCli(["--yes", "--no-install", "--public-key", "pk_test", "--editor", all, "--dir", dir], dir);
  assert.equal(res.status, 0, res.stdout + res.stderr);
  for (const e of EDITORS) assert.ok(existsSync(join(dir, e.file)), e.file);
  for (const d of [".claude/skills", ".agents/skills", ".github/skills", ".kiro/skills", ".junie/skills"]) assert.ok(existsSync(join(dir, d, "likyly/SKILL.md")), d);
  assert.equal(res.stdout.match(/LIKYLY skill → \.agents\/skills/g).length, 1, ".agents/skills is shared: written once");
  assert.match(res.stdout, /trust this folder/);

  const inventory = runCli(["inventory", "--json", "--dir", dir], dir);
  const listed = JSON.parse(inventory.stdout).setup.filter((s) => s.what === "mcp").map((s) => s.path).sort();
  assert.deepEqual(listed, EDITORS.map((e) => e.file).sort());

  const un = runCli(["uninstall", "--yes", "--dir", dir], dir);
  assert.equal(un.status, 0, un.stdout + un.stderr);
  for (const e of EDITORS) assert.equal(existsSync(join(dir, e.file)), false, e.file);
  for (const d of [".claude/skills", ".agents/skills", ".github/skills", ".kiro/skills", ".junie/skills"]) assert.equal(existsSync(join(dir, d, "likyly")), false, d);
});

test("installed assistants: a command on the PATH or a macOS app", () => {
  const home = mkdtempSync(join(tmpdir(), "likyly-home-"));
  mkdirSync(join(home, "Applications", "Kiro.app"), { recursive: true });
  const found = installedEditors({ home, platform: "darwin", commandExists: (bin) => bin === "codex" || bin === "vibe" });
  assert.ok(found.includes("codex") && found.includes("mistral-vibe") && found.includes("kiro"), found.join(","));
  assert.ok(!found.includes("claude-code"));
  assert.deepEqual(installedEditors({ home, platform: "linux", commandExists: () => false }), [], "apps only count on macOS");
});


test("a Shopify app project is recognised and left untouched: no SDK, no provider, no key file", () => {
  const dir = project({ "shopify.app.toml": 'name = "x"\n', ...pkg({ react: "19", "react-router": "7", "@react-router/dev": "7" }), "app/": "" });
  assert.equal(detectProject(dir).id, "shopify-app");
  const res = runCli(["--yes", "--public-key", "pk_test", "--editor", "claude-code", "--dir", dir], dir);
  assert.equal(res.status, 0, res.stdout + res.stderr);
  assert.match(res.stdout, /Shopify app/);
  assert.match(res.stdout, /theme app extension/);
  assert.deepEqual(JSON.parse(readFileSync(join(dir, "package.json"), "utf8")), { dependencies: { react: "19", "react-router": "7", "@react-router/dev": "7" } });
  assert.equal(existsSync(join(dir, "app/components/likyly-provider.tsx")), false);
  assert.equal(existsSync(join(dir, ".env")), false);
  assert.ok(existsSync(join(dir, ".mcp.json")), "the assistant is still connected");
});


test("Claude Code: the likyly server is approved in settings.local.json, other settings kept, removed on uninstall", () => {
  const dir = project({ ...pkg({ next: "16", react: "19" }), "app/": "", ".claude/settings.local.json": { permissions: { allow: ["Bash(ls)"] }, enabledMcpjsonServers: ["other"] } });
  assert.equal(runCli(["--yes", "--no-install", "--public-key", "pk_test", "--editor", "claude-code", "--dir", dir], dir).status, 0);
  const settings = JSON.parse(readFileSync(join(dir, ".claude/settings.local.json"), "utf8"));
  assert.deepEqual(settings.enabledMcpjsonServers, ["other", "likyly"]);
  assert.deepEqual(settings.permissions, { allow: ["Bash(ls)"] });
  assert.equal(approveClaudeServer(dir).status, "same", "never added twice");

  assert.equal(runCli(["uninstall", "--yes", "--dir", dir], dir).status, 0);
  assert.deepEqual(JSON.parse(readFileSync(join(dir, ".claude/settings.local.json"), "utf8")), { permissions: { allow: ["Bash(ls)"] }, enabledMcpjsonServers: ["other"] });
});

test("a developer key counts when exported or in the project's credentials file", () => {
  const dir = project({});
  assert.equal(hasDeveloperKey(dir, {}), false);
  assert.equal(hasDeveloperKey(dir, { LIKYLY_DEVELOPER_KEY: "lk_x" }), true);
  writeFileSync(join(mkdirSync(join(dir, ".likyly"), { recursive: true }) ?? join(dir, ".likyly"), "credentials.env"), "# account\nLIKYLY_DEVELOPER_KEY=lk_y\n");
  assert.equal(hasDeveloperKey(dir, {}), true);
});

// --- Shopify catalog, connected by the wizard (credentials never reach the assistant) ----------

import { catalogName, connectShopifyCatalog, normalizeShopDomain, shopifyCredentials } from "../src/shopify.mjs";

function fakeApi(routes) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    calls.push({ method: init.method, path, body: init.body ? JSON.parse(init.body) : undefined, key: init.headers["X-API-Key"] });
    const [status, data] = routes[`${init.method} ${path}`] ?? [404, { detail: "not found" }];
    return { status, ok: status < 300, json: async () => data };
  };
  return { calls, fetchImpl };
}

test("Shopify: store address and credentials are normalised", () => {
  assert.equal(normalizeShopDomain("My-Shop"), "my-shop.myshopify.com");
  assert.equal(normalizeShopDomain("https://my-shop.myshopify.com/admin/products"), "my-shop.myshopify.com");
  assert.equal(normalizeShopDomain("www.my-shop.com"), null, "a custom domain is not the Shopify address");
  assert.deepEqual(shopifyCredentials("shpat_abc"), { access_token: "shpat_abc" });
  assert.deepEqual(shopifyCredentials(" id ", " shpss_x "), { client_id: "id", client_secret: "shpss_x" });
  assert.equal(shopifyCredentials("id", ""), null);
  assert.equal(catalogName("likyly-sandbox.myshopify.com"), "likyly-sandbox");
});

test("Shopify: a new source is created, tested and synced, with the developer key", async () => {
  const { calls, fetchImpl } = fakeApi({
    "GET /data-sources": [200, []],
    "POST /data-sources": [201, { id: 7, type: "shopify", product_type: "my-shop", config: { shop_domain: "my-shop.myshopify.com" } }],
    "POST /data-sources/7/test": [200, { ok: true, message: "Connected to my-shop.myshopify.com - 1 product(s) on the first page" }],
    "POST /data-sources/7/sync": [202, { id: 1, status: "running" }],
    "GET /data-sources/7/syncs": [200, [{ id: 1, status: "success", items_fetched: 120, items_upserted: 50, items_failed: 0 }]],
  });
  const results = await connectShopifyCatalog({ apiKey: "lk_dev", shop: "my-shop.myshopify.com", credentials: { client_id: "a", client_secret: "b" }, fetchImpl, wait: async () => {} });
  assert.ok(results.every((r) => r.level !== "fail"), JSON.stringify(results));
  assert.ok(results.some((r) => r.text === "50 product(s) synced to LIKYLY"), "the real outcome, not just 'started'");
  const create = calls.find((c) => c.method === "POST" && c.path === "/data-sources");
  assert.deepEqual(create.body, { name: "Shopify · my-shop.myshopify.com", type: "shopify", product_type: "my-shop", config: { shop_domain: "my-shop.myshopify.com" }, credentials: { client_id: "a", client_secret: "b" } });
  assert.deepEqual(calls.find((c) => c.path === "/data-sources/7/sync").body, { mode: "full" });
  assert.ok(calls.every((c) => c.key === "lk_dev"));
  assert.doesNotMatch(JSON.stringify(results), /shpss_|"b"/, "the secret is never echoed");
});

test("Shopify: a source that fails its test is deleted again; an existing one for the shop is updated", async () => {
  const failing = fakeApi({
    "GET /data-sources": [200, []],
    "POST /data-sources": [201, { id: 8, type: "shopify", product_type: "s", config: { shop_domain: "s.myshopify.com" } }],
    "POST /data-sources/8/test": [200, { ok: false, message: "Shopify refused the client id and secret (400)" }],
    "DELETE /data-sources/8": [200, { message: "deleted" }],
  });
  const r1 = await connectShopifyCatalog({ apiKey: "lk", shop: "s.myshopify.com", credentials: { client_id: "a", client_secret: "b" }, fetchImpl: failing.fetchImpl });
  assert.equal(r1[0].level, "fail");
  assert.match(r1[0].text, /refused the client id and secret/);
  assert.equal(r1[0].retry, true, "the step asks for the store and credentials again");
  assert.ok(failing.calls.some((c) => c.method === "DELETE" && c.path === "/data-sources/8"));

  const existing = fakeApi({
    "GET /data-sources": [200, [{ id: 3, type: "shopify", product_type: "s", config: { shop_domain: "s.myshopify.com" } }]],
    "PATCH /data-sources/3": [200, {}],
    "POST /data-sources/3/test": [200, { ok: true, message: "ok" }],
    "POST /data-sources/3/sync": [429, { detail: "One sync per day on the free plan" }],
  });
  const r2 = await connectShopifyCatalog({ apiKey: "lk", shop: "s.myshopify.com", credentials: { access_token: "shpat_x" }, fetchImpl: existing.fetchImpl, wait: async () => {} });
  assert.ok(!existing.calls.some((c) => c.method === "POST" && c.path === "/data-sources"), "no second source");
  assert.deepEqual(existing.calls.find((c) => c.method === "PATCH").body, { credentials: { access_token: "shpat_x" } });
  assert.ok(r2.some((r) => r.level === "fail" && /could not start: One sync per day/.test(r.text)));
});

test("Shopify: new credentials for an existing source are tested first, and a refusal keeps the old ones", async () => {
  const { calls, fetchImpl } = fakeApi({
    "GET /data-sources": [200, [{ id: 3, type: "shopify", product_type: "s", config: { shop_domain: "s.myshopify.com" } }]],
    "POST /data-sources/3/test": [200, { ok: false, message: "Shopify refused the client id and secret (400)" }],
  });
  const r = await connectShopifyCatalog({ apiKey: "lk", shop: "s.myshopify.com", credentials: { client_id: "a", client_secret: "typo" }, fetchImpl });
  assert.equal(r[0].retry, true);
  assert.deepEqual(calls.find((c) => c.path === "/data-sources/3/test").body, { credentials: { client_id: "a", client_secret: "typo" } });
  assert.ok(!calls.some((c) => c.method === "PATCH" || c.method === "DELETE"), "the working source is left as it was");
});

test("Shopify: an API error is reported with its own message", async () => {
  const { fetchImpl } = fakeApi({ "GET /data-sources": [200, []], "POST /data-sources": [503, { detail: "LIKYLY cannot store data source credentials right now" }] });
  const r = await connectShopifyCatalog({ apiKey: "lk", shop: "s.myshopify.com", credentials: { access_token: "shpat_x" }, fetchImpl });
  assert.match(r[0].text, /cannot store data source credentials/);
});

test("shopify command: needs a terminal", () => {
  const dir = project({});
  const res = runCli(["shopify", "--yes", "--dir", dir], dir);
  assert.equal(res.status, 1);
  assert.match(res.stderr, /run it in a terminal/);
});

test("Shopify: a sync that saves nothing says why (e.g. the plan's catalog limit)", async () => {
  const { fetchImpl } = fakeApi({
    "GET /data-sources": [200, []],
    "POST /data-sources": [201, { id: 4, type: "shopify", product_type: "s", config: { shop_domain: "s.myshopify.com" } }],
    "POST /data-sources/4/test": [200, { ok: true, message: "ok" }],
    "POST /data-sources/4/sync": [202, { id: 5, status: "running" }],
    "GET /data-sources/4/syncs": [200, [{ id: 5, status: "failed", items_fetched: 120, items_upserted: 0, items_failed: 120, error_summary: JSON.stringify([{ stage: "upsert", message: "Free plan limit reached: 1 catalog max." }]) }]],
  });
  const r = await connectShopifyCatalog({ apiKey: "lk", shop: "s.myshopify.com", credentials: { access_token: "shpat_x" }, fetchImpl, wait: async () => {} });
  const failure = r.find((x) => x.level === "fail");
  assert.equal(failure.text, "No product saved in LIKYLY (120 rejected): Free plan limit reached: 1 catalog max.");
});

import { openInNewTerminal, previewPlan, themeStore } from "../src/preview.mjs";

const THEME = { "layout/theme.liquid": "<html></html>", "config/settings_schema.json": "[]", "sections/main-product.liquid": "" };

test("preview: a Shopify theme previews against its store, found without asking", async () => {
  const noApi = async () => ({ ok: false });
  const toml = project({ ...THEME, "shopify.theme.toml": '[environments.dev]\nstore = "my-shop"\n' });
  assert.equal((await previewPlan(detectProject(toml), toml, { fetchImpl: noApi, env: {} })).command, "shopify theme dev --store my-shop.myshopify.com");
  assert.equal(await themeStore(toml, { store: "https://other.myshopify.com/admin", env: {} }), "other.myshopify.com", "--store wins");

  // No toml: the store of the developer's LIKYLY Shopify source.
  const dir = project({ ...THEME, ".likyly/credentials.env": "LIKYLY_DEVELOPER_KEY=lk_dev\n" });
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push([url, init.headers["X-API-Key"]]);
    return { ok: true, json: async () => [{ type: "rest_api" }, { type: "shopify", config: { shop_domain: "likyly-sandbox.myshopify.com" } }] };
  };
  const plan = await previewPlan(detectProject(dir), dir, { fetchImpl, env: {} });
  assert.equal(plan.command, "shopify theme dev --store likyly-sandbox.myshopify.com");
  assert.equal(plan.requires, "shopify");
  assert.ok(plan.notes.some((n) => n.includes("https://admin.shopify.com/store/likyly-sandbox/online_store/preferences") && /not the app's client secret/.test(n)));
  assert.match(seen[0][0], /\/data-sources$/);
  assert.equal(seen[0][1], "lk_dev");

  const unknown = project(THEME);
  assert.equal((await previewPlan(detectProject(unknown), unknown, { env: {} })).command, "shopify theme dev", "the CLI asks for the store itself");
});

test("preview: a JS project runs its own dev script; none known otherwise", async () => {
  const next = project({ "package.json": { dependencies: { next: "16", react: "19" }, scripts: { dev: "next dev" } }, "pnpm-lock.yaml": "", "app/": "" });
  assert.equal((await previewPlan(detectProject(next), next)).command, "pnpm dev");
  const npm = project({ "package.json": { dependencies: { vue: "3" }, scripts: { dev: "vite" } } });
  assert.equal((await previewPlan(detectProject(npm), npm)).command, "npm run dev");
  const bare = project({ "package.json": { dependencies: { vue: "3" } } });
  assert.equal(await previewPlan(detectProject(bare), bare), null);
  assert.equal(await previewPlan(null, bare), null);
});

test("preview: opened in a new terminal window, the folder quoted", () => {
  const calls = [];
  const run = (cmd, args) => (calls.push([cmd, args]), { status: 0 });
  assert.equal(openInNewTerminal("shopify theme dev --store s.myshopify.com", "/tmp/it's here", { platform: "darwin", run }), true);
  const [cmd, args] = calls[0];
  assert.equal(cmd, "osascript");
  assert.match(args[1], /^tell application "Terminal" to do script "cd '\/tmp\/it'\\\\''s here' && .* && shopify theme dev --store s\.myshopify\.com"$/);

  const none = (cmd) => ({ status: cmd === "which" ? 1 : 0 });
  assert.equal(openInNewTerminal("npm run dev", "/tmp", { platform: "linux", run: none }), false, "no terminal emulator: the caller prints the command");
});

test("preview command: nothing known is a clear error, not a hang", () => {
  const dir = project({ "README.md": "" });
  const res = runCli(["preview", "--dir", dir], dir);
  assert.equal(res.status, 1);
  assert.match(res.stderr, /No preview command known/);
  assert.equal(parseArgs(["preview"]).here, false, "a new window by default: the terminal stays free");
  assert.equal(parseArgs(["preview", "--here"]).here, true);
  assert.equal(parseArgs(["preview", "--store", "x", "--new-window"]).here, false);
});

// --- Magento: a module written by the wizard, the catalog read through an Integration ----------

import { connectMagentoCatalog, magentoCatalogName, magentoCredentials, normalizeStoreUrl } from "../src/magento.mjs";
import { planUninstall } from "../src/uninstall.mjs";

const magentoStore = (require = { "magento/product-community-edition": "2.4.9" }) =>
  project({ "composer.json": { name: "magento/project-community-edition", require: { ...require, "symfony/console": "^6" } }, "bin/magento": "", "app/etc/di.xml": "<config/>", "app/code/": "" });

test("detects Magento Open Source, Adobe Commerce and a magento2 clone, before generic PHP", () => {
  const open = detectProject(magentoStore());
  assert.equal(open.id, "magento");
  assert.equal(open.label, "Magento Open Source");
  assert.equal(open.install, null, "nothing for Composer: the module is written in app/code");
  assert.equal(open.starter.path, "app/code/Likyly/Recommendations");
  assert.equal(open.keyFile, "app/code/Likyly/Recommendations/etc/config.xml");
  assert.equal(detectProject(magentoStore({ "magento/product-enterprise-edition": "2.4.9" })).label, "Adobe Commerce");
  const clone = project({ "composer.json": { name: "magento/magento2ce", require: { php: "~8.3" } }, "bin/magento": "", "app/etc/di.xml": "" });
  assert.equal(detectProject(clone).id, "magento");
  assert.equal(detectProject(project({ "composer.json": { require: { php: "8" } }, "bin/magento": "" })).id, "php", "bin/magento alone is not enough");
});

test("end to end: a Magento store gets the module and its key, nothing installed", () => {
  const dir = magentoStore();
  const res = runCli(["--yes", "--public-key", "pk_test_mage", "--dir", dir], dir);
  assert.equal(res.status, 0, res.stderr);
  assert.match(res.stdout, /Magento Open Source/);
  assert.match(res.stdout, /bin\/magento module:enable Likyly_Recommendations/);
  const mod = join(dir, "app/code/Likyly/Recommendations");
  for (const f of ["registration.php", "etc/module.xml", "etc/config.xml", "etc/widget.xml", "etc/adminhtml/system.xml", "Block/Recommendations.php", "view/frontend/templates/recommendations.phtml"]) {
    assert.ok(existsSync(join(mod, f)), f);
  }
  const config = readFileSync(join(mod, "etc/config.xml"), "utf8");
  assert.match(config, /<public_key>pk_test_mage<\/public_key>/);
  assert.doesNotMatch(config, /api_url/, "production: no API address written");
  // The storefront script is valid JavaScript once taken out of the template.
  const phtml = readFileSync(join(mod, "view/frontend/templates/recommendations.phtml"), "utf8");
  const script = phtml.match(/<<<'JS'\n([\s\S]*?)\nJS;/)[1].replace(/^\s*import .*$/m, "");
  const check = spawnSync(process.execPath, ["--input-type=module", "--check"], { input: script, encoding: "utf8" });
  assert.equal(check.status, 0, check.stderr);
  assert.match(phtml, /data-likyly-placement=/);
  assert.match(phtml, /li\.dataset\.likylyItemId/);
  // PHP files parse, when PHP is around.
  if (spawnSync("php", ["-v"]).status === 0) {
    for (const f of ["registration.php", "Block/Recommendations.php", "view/frontend/templates/recommendations.phtml"]) {
      const lint = spawnSync("php", ["-l", join(mod, f)], { encoding: "utf8" });
      assert.equal(lint.status, 0, lint.stdout + lint.stderr);
    }
  }

  // A new key rewrites config.xml only; a local API address is written next to it.
  const again = spawnSync(process.execPath, [BIN, "--yes", "--public-key", "pk_test_new", "--dir", dir], { cwd: dir, encoding: "utf8", env: { ...process.env, LIKYLY_API_URL: "http://127.0.0.1:6061", CI: "1" } });
  assert.equal(again.status, 0, again.stderr);
  const updated = readFileSync(join(mod, "etc/config.xml"), "utf8");
  assert.match(updated, /pk_test_new/);
  assert.match(updated, /<api_url>http:\/\/127\.0\.0\.1:6061<\/api_url>/);

  // Uninstall takes the whole module out (the key file with it), as long as nobody edited it.
  const plan = planUninstall(dir);
  assert.ok(plan.steps.some((s) => s.label === "Delete app/code/Likyly/Recommendations"));
  assert.ok(!plan.steps.some((s) => /etc\/config\.xml/.test(s.label)), "the key file goes with the module");
  assert.deepEqual(plan.code, []);
  writeFileSync(join(mod, "Block/Recommendations.php"), "<?php // edited\n");
  const kept = planUninstall(dir);
  assert.ok(kept.kept.some((k) => /Recommendations \(edited/.test(k)));
});

test("Magento: store address, catalog name and the Integration's four values", () => {
  assert.equal(normalizeStoreUrl("shop.example.com"), "https://shop.example.com");
  assert.equal(normalizeStoreUrl("https://shop.example.com/admin/catalog/product/"), "https://shop.example.com");
  assert.equal(normalizeStoreUrl("http://localhost:8896/"), "http://localhost:8896");
  assert.equal(normalizeStoreUrl("localhost:8896"), "http://localhost:8896");
  assert.equal(normalizeStoreUrl("https://example.com/fr/rest/V1/products"), "https://example.com/fr");
  assert.equal(normalizeStoreUrl("not a url"), null);
  assert.equal(magentoCatalogName("https://www.shop.example.com"), "shop-example-com");
  assert.equal(magentoCatalogName("http://localhost:8896"), "magento");
  assert.deepEqual(magentoCredentials({ consumerKey: " a ", consumerSecret: "b", accessToken: "c", accessTokenSecret: "d" }), { consumer_key: "a", consumer_secret: "b", access_token: "c", access_token_secret: "d" });
  assert.equal(magentoCredentials({ consumerKey: "a", consumerSecret: "b", accessToken: "c" }), null);
});

test("Magento: a new source is created, tested and synced; a refusal deletes it and asks again", async () => {
  const credentials = { consumer_key: "a", consumer_secret: "b", access_token: "c", access_token_secret: "d" };
  const { calls, fetchImpl } = fakeApi({
    "GET /data-sources": [200, []],
    "POST /data-sources": [201, { id: 9, type: "magento", product_type: "shop-example-com", config: { store_url: "https://shop.example.com" } }],
    "POST /data-sources/9/test": [200, { ok: true, message: "Connected to https://shop.example.com - 120 product(s) listed (signed requests (OAuth))" }],
    "POST /data-sources/9/sync": [202, { id: 2, status: "running" }],
    "GET /data-sources/9/syncs": [200, [{ id: 2, status: "success", items_fetched: 120, items_upserted: 120, items_failed: 0 }]],
  });
  const results = await connectMagentoCatalog({ apiKey: "lk_dev", storeUrl: "https://shop.example.com", credentials, fetchImpl, wait: async () => {} });
  assert.ok(results.every((r) => r.level !== "fail"), JSON.stringify(results));
  assert.ok(results.some((r) => r.text === "120 product(s) synced to LIKYLY"));
  assert.deepEqual(calls.find((c) => c.method === "POST" && c.path === "/data-sources").body, {
    name: "Magento · shop.example.com", type: "magento", product_type: "shop-example-com", config: { store_url: "https://shop.example.com" }, credentials,
  });
  assert.doesNotMatch(JSON.stringify(results), /"b"|"d"/, "the secrets are never echoed");

  const refused = fakeApi({
    "GET /data-sources": [200, []],
    "POST /data-sources": [201, { id: 4, type: "magento", product_type: "m", config: { store_url: "https://m.example.com" } }],
    "POST /data-sources/4/test": [200, { ok: false, message: "Magento refused the Integration's credentials (unauthorized)" }],
    "DELETE /data-sources/4": [200, {}],
  });
  const r = await connectMagentoCatalog({ apiKey: "lk", storeUrl: "https://m.example.com", credentials, fetchImpl: refused.fetchImpl });
  assert.equal(r[0].retry, true);
  assert.match(r[0].text, /^Magento refused the connection: /);
  assert.ok(refused.calls.some((c) => c.method === "DELETE" && c.path === "/data-sources/4"));
});

test("catalog step: the store already connected is found first, with its product count", async () => {
  const { findConnectedSource, describeSource } = await import("../src/catalog.mjs");
  const { calls, fetchImpl } = fakeApi({
    "GET /data-sources": [200, [{ id: 2, type: "webhook", product_type: "x" }, { id: 5, type: "shopify", product_type: "likyly-sandbox", status: "connected", config: { shop_domain: "likyly-sandbox.myshopify.com" } }]],
    "GET /data-sources/5/stats": [200, { item_count: 50 }],
  });
  const found = await findConnectedSource({ apiKey: "lk", type: "shopify", fetchImpl });
  assert.equal(found.id, 5);
  assert.equal(describeSource(found.config.shop_domain, found), "likyly-sandbox.myshopify.com → catalog likyly-sandbox · 50 product(s)");
  assert.ok(calls.every((c) => c.method === "GET"), "only reads");
  assert.equal(await findConnectedSource({ apiKey: "lk", type: "magento", fetchImpl }), null);
  const down = async () => {
    throw new Error("offline");
  };
  assert.equal(await findConnectedSource({ apiKey: "lk", type: "shopify", fetchImpl: down }), null, "LIKYLY unreachable: just ask for the store");
});
