import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { detectProject, detectEditors } from "../src/frameworks.mjs";
import { mergeConfig, writeMcpConfig } from "../src/mcp.mjs";
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
  assert.match(readFileSync(join(dir, ".claude/commands/likyly-clear-cache.md"), "utf8"), /npx -y @likyly\/wizard cache clear \$ARGUMENTS/);
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
  for (const gone of [".claude/skills/likyly", ".claude/commands/likyly-clear-cache.md", ".claude/commands/likyly-uninstall.md", ".likyly"]) {
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
