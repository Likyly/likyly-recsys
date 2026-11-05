// `likyly-wizard inventory`: what LIKYLY put in this project, read-only. The setup the wizard
// wrote (packages, key variable, provider, assistant config, commands, credentials file) and
// every source line that mentions LIKYLY, so /likyly-inventory can tell which page shows which
// recommendation block. Key values are never read out: only the variable names.

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { detectProject, EDITORS } from "./frameworks.mjs";
import { configPath } from "./mcp.mjs";
import { CLAUDE_COMMANDS, starterContent } from "./setup.mjs";

const ENV_FILES = [".env", ".env.local", ".env.development", ".env.production"];
const KEY_VAR = /^\s*((?:NEXT_PUBLIC_|VITE_|PUBLIC_|NUXT_PUBLIC_|REACT_APP_)?LIKYLY_[A-Z_]*)\s*=/;
const SKIP_DIRS = new Set(["node_modules", ".git", ".next", ".nuxt", ".svelte-kit", ".astro", "dist", "build", "out", ".likyly", ".claude", "vendor", ".venv", "coverage"]);
const SOURCE = /\.(m?[jt]sx?|vue|svelte|astro|php|py|rb|css|scss)$/;
const MAX_FILES = 40;
const MAX_LINES_PER_FILE = 15;

/** Source files mentioning LIKYLY, with the matching lines: [{ file, lines: [{ line, text }] }]. */
export function likylyCode(dir, { skip = new Set() } = {}) {
  const found = [];
  const walk = (folder, depth) => {
    if (depth > 8 || found.length >= MAX_FILES) return;
    let entries = [];
    try {
      entries = readdirSync(folder).sort();
    } catch {
      return;
    }
    for (const name of entries) {
      if (SKIP_DIRS.has(name) || found.length >= MAX_FILES) continue;
      const path = join(folder, name);
      let st;
      try {
        st = statSync(path);
      } catch {
        continue;
      }
      if (st.isDirectory()) walk(path, depth + 1);
      else if (SOURCE.test(name) && st.size < 512_000 && !skip.has(path)) {
        const text = readFileSync(path, "utf8");
        if (!/likyly/i.test(text)) continue;
        const lines = [];
        text.split("\n").forEach((content, i) => {
          if (/likyly/i.test(content) && lines.length < MAX_LINES_PER_FILE) lines.push({ line: i + 1, text: content.trim().slice(0, 160) });
        });
        found.push({ file: relative(dir, path), lines });
      }
    }
  };
  walk(dir, 0);
  return found;
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

/** Everything LIKYLY in this project. Pure read: nothing is written, no key value is returned. */
export function takeInventory(dir) {
  const project = detectProject(dir);
  const pkg = readJson(join(dir, "package.json"));
  const deps = pkg ? { ...pkg.dependencies, ...pkg.devDependencies } : {};
  const packages = Object.keys(deps)
    .filter((name) => name.startsWith("@likyly/"))
    .map((name) => ({ name, version: readJson(join(dir, "node_modules", name, "package.json"))?.version ?? null, wanted: deps[name] }));

  const keys = [];
  for (const file of ENV_FILES) {
    const path = join(dir, file);
    if (!existsSync(path)) continue;
    for (const line of readFileSync(path, "utf8").split("\n")) {
      const m = line.match(KEY_VAR);
      if (m) keys.push({ file, variable: m[1] });
    }
  }

  const setup = [];
  if (project?.starter) {
    const path = join(dir, project.starter.path);
    if (existsSync(path)) {
      const untouched = readFileSync(path, "utf8") === starterContent(project.starter);
      setup.push({ what: "provider", path: project.starter.path, note: untouched ? "as the wizard wrote it" : "edited since the wizard wrote it" });
    }
  }
  for (const editor of EDITORS) {
    const path = configPath(dir, editor.id);
    const config = existsSync(path) ? readJson(path) : null;
    const key = editor.id === "vscode" ? "servers" : "mcpServers";
    if (config?.[key]?.likyly) setup.push({ what: "mcp", path: relative(dir, path), note: editor.label ?? editor.id });
  }
  if (existsSync(join(dir, ".claude/skills/likyly"))) setup.push({ what: "skill", path: ".claude/skills/likyly" });
  for (const file of CLAUDE_COMMANDS) {
    if (existsSync(join(dir, ".claude/commands", file))) setup.push({ what: "command", path: `.claude/commands/${file}`, note: `/${file.replace(/\.md$/, "")}` });
  }

  let account = null;
  const credentials = join(dir, ".likyly", "credentials.env");
  if (existsSync(credentials)) {
    const text = readFileSync(credentials, "utf8");
    const field = (name) => text.match(new RegExp(`^# ${name}:\\s*(.+)$`, "m"))?.[1]?.trim() ?? null;
    account = { account: field("Account"), workspace: field("Workspace"), console: field("Console") };
    setup.push({ what: "credentials", path: ".likyly/credentials.env", note: "your assistant's developer key, outside git" });
  }

  return {
    project: project ? { framework: project.label ?? project.id ?? null, packageManager: project.packageManager ?? null } : null,
    account,
    packages,
    keys,
    setup,
    code: likylyCode(dir),
  };
}

const LABEL = { provider: "Provider", mcp: "Assistant connection (MCP)", skill: "Claude Code skill", command: "Claude Code command", credentials: "Developer key file" };

/** Plain-text rendering for the terminal and for /likyly-inventory. */
export function formatInventory(inv, dir) {
  const out = [`LIKYLY inventory · ${dir}`];
  if (inv.account) out.push(`Account: ${inv.account.account ?? "?"} · Workspace: ${inv.account.workspace ?? "?"} · Console: ${inv.account.console ?? "?"}`);
  if (inv.project?.framework) out.push(`Framework: ${inv.project.framework}`);
  out.push("", "Packages");
  if (inv.packages.length) for (const p of inv.packages) out.push(`  - ${p.name} ${p.version ?? "(not installed)"}${p.wanted ? ` (package.json: ${p.wanted})` : ""}`);
  else out.push("  - none");
  out.push("", "Public key (variable names only, never the value)");
  if (inv.keys.length) for (const k of inv.keys) out.push(`  - ${k.variable} in ${k.file}`);
  else out.push("  - none");
  out.push("", "Setup");
  if (inv.setup.length) for (const s of inv.setup) out.push(`  - ${LABEL[s.what] ?? s.what}: ${s.path}${s.note ? ` (${s.note})` : ""}`);
  else out.push("  - none");
  out.push("", "Code that mentions LIKYLY");
  if (!inv.code.length) out.push("  - none");
  for (const f of inv.code) {
    out.push(`  ${f.file}`);
    for (const l of f.lines) out.push(`    ${l.line}: ${l.text}`);
  }
  return out.join("\n");
}
