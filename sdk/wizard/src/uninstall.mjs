// `npx @likyly/wizard uninstall`: take out what the wizard put in, nothing else. Files the
// developer (or their assistant) changed are kept and listed; code that calls LIKYLY is listed,
// never edited; the LIKYLY account (catalog, placements, keys) is untouched.

import { existsSync, readFileSync, readdirSync, rmSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { detectProject, EDITORS } from "./frameworks.mjs";
import { configPath } from "./mcp.mjs";
import { CLAUDE_COMMANDS, commandExists, runAsync, starterContent } from "./setup.mjs";

const ENV_FILES = [".env", ".env.local"];
const KEY_LINE = /^\s*(NEXT_PUBLIC_|VITE_|PUBLIC_|NUXT_PUBLIC_|REACT_APP_)?LIKYLY_KEY\s*=.*$/;
const KEY_COMMENT = "# LIKYLY public key (safe in the browser)";
const SKIP_DIRS = new Set(["node_modules", ".git", ".next", "dist", "build", ".likyly", ".claude", "vendor", ".venv"]);

/** Source files that still import or call LIKYLY (reported, never edited). */
function codeUsingLikyly(dir, keep) {
  const found = [];
  const walk = (folder, depth) => {
    if (depth > 6 || found.length >= 20) return;
    let entries = [];
    try {
      entries = readdirSync(folder);
    } catch {
      return;
    }
    for (const name of entries) {
      if (SKIP_DIRS.has(name)) continue;
      const path = join(folder, name);
      let st;
      try {
        st = statSync(path);
      } catch {
        continue;
      }
      if (st.isDirectory()) walk(path, depth + 1);
      else if (/\.(m?[jt]sx?|vue|svelte|astro|php|py|rb)$/.test(name) && st.size < 512_000 && !keep.has(path)) {
        if (/@likyly\/|from ["']likyly|LikylyProvider|likyly\.recommend/.test(readFileSync(path, "utf8"))) found.push(relative(dir, path));
      }
    }
  };
  walk(dir, 0);
  return found;
}

/**
 * What uninstall would do: `{ steps: [{ label, run }], kept: [text], code: [file] }`.
 * Each `run` is synchronous except the package removal.
 */
export function planUninstall(dir) {
  const steps = [];
  const kept = [];
  const project = detectProject(dir);

  // SDK packages, with the project's own package manager.
  const pkg = (() => {
    try {
      return JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
    } catch {
      return null;
    }
  })();
  const deps = pkg ? { ...pkg.dependencies, ...pkg.devDependencies } : {};
  const packages = ["@likyly/sdk", "@likyly/react"].filter((p) => deps[p]);
  if (packages.length) {
    const pm = project?.packageManager ?? "npm";
    const cmd = [pm, pm === "npm" ? "uninstall" : "remove", ...packages];
    steps.push({ label: `Remove ${packages.join(" + ")} (${cmd.join(" ")})`, cmd });
  }

  // The public key line(s) the wizard added.
  for (const file of ENV_FILES) {
    const path = join(dir, file);
    if (!existsSync(path)) continue;
    const lines = readFileSync(path, "utf8").split("\n");
    const rest = lines.filter((l) => !KEY_LINE.test(l) && l.trim() !== KEY_COMMENT);
    if (rest.length !== lines.length) {
      steps.push({ label: `Remove the LIKYLY key from ${file}`, run: () => writeFileSync(path, rest.join("\n")) });
    }
  }

  // The starter file, only if nobody changed it.
  const keepFiles = new Set();
  if (project?.starter) {
    const path = join(dir, project.starter.path);
    if (existsSync(path)) {
      if (readFileSync(path, "utf8") === starterContent(project.starter)) steps.push({ label: `Delete ${project.starter.path}`, run: () => unlinkSync(path) });
      else {
        kept.push(`${project.starter.path} (edited since the wizard wrote it)`);
        keepFiles.add(path);
      }
    }
  }

  // The likyly server in each assistant's MCP config; a file left with nothing else goes too.
  for (const editor of EDITORS) {
    const path = configPath(dir, editor.id);
    if (!existsSync(path)) continue;
    let config;
    try {
      config = JSON.parse(readFileSync(path, "utf8"));
    } catch {
      kept.push(`${relative(dir, path)} (not valid JSON: remove the "likyly" server by hand)`);
      continue;
    }
    const key = editor.id === "vscode" ? "servers" : "mcpServers";
    if (!config?.[key]?.likyly) continue;
    delete config[key].likyly;
    const empty = !Object.keys(config[key]).length && Object.keys(config).every((k) => k === key || (k === "inputs" && !config.inputs?.length));
    steps.push({
      label: empty ? `Delete ${relative(dir, path)} (only LIKYLY was in it)` : `Remove the likyly server from ${relative(dir, path)}`,
      run: () => (empty ? unlinkSync(path) : writeFileSync(path, JSON.stringify(config, null, 2) + "\n")),
    });
  }

  // Claude Code skill and commands, and the credentials folder.
  for (const rel of [".claude/skills/likyly", ...CLAUDE_COMMANDS.map((f) => `.claude/commands/${f}`), ".likyly"]) {
    const path = join(dir, rel);
    if (existsSync(path)) steps.push({ label: `Delete ${rel}${rel === ".likyly" ? " (your developer key for this project)" : ""}`, run: () => rmSync(path, { recursive: true, force: true }) });
  }

  return { steps, kept, code: codeUsingLikyly(dir, keepFiles) };
}

/** Run the plan. Returns [{ ok, label }]. */
export async function runUninstall(dir, plan, onLine) {
  const results = [];
  for (const step of plan.steps) {
    try {
      if (step.cmd) {
        if (!commandExists(step.cmd[0])) {
          results.push({ ok: false, label: `${step.cmd[0]} not found: run ${step.cmd.join(" ")} yourself` });
          continue;
        }
        const ok = await runAsync(dir, step.cmd, onLine);
        results.push({ ok, label: ok ? step.label : `${step.label} failed: run ${step.cmd.join(" ")}` });
      } else {
        step.run();
        results.push({ ok: true, label: step.label });
      }
    } catch (err) {
      results.push({ ok: false, label: `${step.label}: ${err.message}` });
    }
  }
  return results;
}
