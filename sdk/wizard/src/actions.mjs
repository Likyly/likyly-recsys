// The wizard's steps as plain functions returning results, so the Ink UI and the plain CI
// renderer run exactly the same code. A result is { level: "ok" | "warn" | "fail", text, hint? }.

import { relative } from "node:path";
import { EDITORS } from "./frameworks.mjs";
import { configPath, writeMcpConfig } from "./mcp.mjs";
import { commandExists, runAsync, writeClaudeCommands, writeCredentials, writeEnv, writeSkill, writeStarter } from "./setup.mjs";
import { checkCompat, describeCompat } from "./compat.mjs";
import { checkKey, installedPackages } from "./verify.mjs";
import { existsSync } from "node:fs";
import { DOCS_URL, WP_PLUGIN_URL } from "./constants.mjs";

const ok = (text, hint) => ({ level: "ok", text, hint });
const warn = (text, hint) => ({ level: "warn", text, hint });
const fail = (text, hint) => ({ level: "fail", text, hint });
/** Nothing to do: already in place. Neutral, so it never reads as a problem. */
const info = (text, hint) => ({ level: "info", text, hint });

export const editorLabel = (id) => EDITORS.find((e) => e.id === id)?.label ?? id;

/** What the SDK step will do, before doing it: "run" | "manual" | "plugin" | "none". */
export function installMode(opts, project) {
  if (project.plugin) return "plugin";
  if (!project.install) return "none";
  if (!project.install.run || !opts.install) return "manual";
  if (!opts.dryRun && !commandExists(project.install.cmd[0])) return "missing";
  return "run";
}

/** Install the SDK packages. `onLine` receives the package manager's output. */
export async function installSdk(opts, project, onLine) {
  const line = project.install?.cmd.join(" ");
  switch (installMode(opts, project)) {
    case "plugin":
      return [warn("WordPress: install the Likyly Connector plugin, then set your key in Settings › Likyly.", WP_PLUGIN_URL)];
    case "none":
      return [];
    case "manual":
      return [warn("Run it yourself:", line)];
    case "missing":
      return [warn(`${project.install.cmd[0]} not found. Run it yourself:`, line)];
  }
  if (opts.dryRun) return [ok(`Would run ${line}`)];
  const output = [];
  const success = await runAsync(opts.dir, project.install.cmd, (l) => {
    output.push(l);
    onLine?.(l);
  });
  if (success) return [ok(`Installed ${project.packages.join(" + ")}`)];
  // The live log is gone once the step ends: keep the package manager's own reason.
  const reason = output.find((l) => /error code|ERR!|ERESOLVE|E404|ENOTFOUND|error:/i.test(l));
  return [fail("Install failed. Run it again:", line), ...(reason ? [warn(reason.replace(/^npm error\s*/i, "").trim().slice(0, 140))] : [])];
}

/** Write the public key to the env file the framework exposes to the browser. */
export function applyKey(opts, project, key) {
  if (!project.envFile || !project.envVar) return [warn("Add your public key to the app configuration.", DOCS_URL)];
  if (!key) return [warn(`Add it later to ${project.envFile}:`, `${project.envVar}=pk_...`)];
  const res = writeEnv(opts.dir, project.envFile, project.envVar, key, { dryRun: opts.dryRun });
  const mask = (k) => (k.length > 14 ? `${k.slice(0, 8)}…${k.slice(-4)}` : k);
  if (res.status === "same") return [info(`${project.envVar} in ${project.envFile} already holds this key`)];
  if (res.status === "updated") return [ok(`${project.envVar} updated in ${project.envFile}`, `(replaces ${mask(res.previous)}, no longer valid)`)];
  return [ok(`${project.envVar} → ${project.envFile}`)];
}

/** Write the framework's starter file (provider, plugin, module), never over an existing file. */
export function applyStarter(opts, project) {
  const starter = writeStarter(opts.dir, project.starter, { dryRun: opts.dryRun });
  if (!starter) return [];
  const rel = relative(opts.dir, starter.path);
  return starter.status === "created" ? [ok(`Starter file ${rel}`)] : [info(`${rel} already in place, left as is`)];
}

/** Connect each chosen AI assistant to the LIKYLY MCP server (+ the skill for Claude Code). */
export function connectEditors(opts, editors) {
  const results = [];
  for (const editor of editors) {
    try {
      const { path, replaced } = writeMcpConfig(opts.dir, editor, { hosted: opts.hosted, dryRun: opts.dryRun });
      results.push(ok(`${editorLabel(editor)} → ${relative(opts.dir, path)}${replaced ? " (updated)" : ""}`));
    } catch (err) {
      results.push(fail(err.message));
    }
  }
  if (editors.includes("claude-code")) {
    const skill = writeSkill(opts.dir, { dryRun: opts.dryRun });
    if (skill) results.push(ok(`LIKYLY skill → ${relative(opts.dir, skill.path)}`));
    const command = writeClaudeCommands(opts.dir, { dryRun: opts.dryRun });
    results.push(ok(`Claude Code commands ${command.names.join(", ")} → ${relative(opts.dir, command.path)}`));
  }
  return results;
}

/** Files and commands the install will touch, shown before the developer confirms. */
export function describePlan(opts, project) {
  const rows = [];
  const mode = installMode(opts, project);
  if (mode === "run") rows.push(["Run", project.install.cmd.join(" ")]);
  else if (mode === "manual" || mode === "missing") rows.push(["Show", project.install.cmd.join(" ")]);
  else if (mode === "plugin") rows.push(["Plugin", "Likyly Connector (WordPress)"]);
  if (project.envFile && project.envVar) rows.push(["Key", `${project.envVar} → ${project.envFile}`]);
  if (project.starter) rows.push(["Create", project.starter.path]);
  rows.push(["Connect", "your AI assistant to the LIKYLY MCP server"]);
  return rows;
}

/** Store the developer key where the MCP server finds it, outside git. */
export function applyDeveloperKey(opts, devKey, origin = {}) {
  if (!devKey) return [];
  const { path } = writeCredentials(opts.dir, devKey, { dryRun: opts.dryRun, ...origin });
  return [ok(`Developer key → ${relative(opts.dir, path)}`, "(never committed)")];
}

const KEY_CHECK = {
  public: ["Public key accepted by the LIKYLY API", "The LIKYLY API rejected the public key", "public key"],
  developer: ["Developer key accepted: your assistant can manage catalogs and placements", "The LIKYLY API rejected the developer key", "developer key"],
};

/** The final check: SDK installed, stack supported, keys accepted, assistants connected. */
export async function verifyInstall(opts, project, { publicKey, devKey, editors = [] }) {
  const results = [];
  if (project?.language === "typescript" && project.packages.length && !opts.dryRun) {
    const pkgs = installedPackages(opts.dir, project.packages);
    const missing = pkgs.filter((p) => !p.version);
    results.push(
      missing.length
        ? warn(`Not installed yet: ${missing.map((p) => p.name).join(", ")}. Run:`, project.install.cmd.join(" "))
        : ok(`SDK installed: ${pkgs.map((p) => `${p.name} ${p.version}`).join(", ")}`),
    );
  }
  if (project) {
    const compat = checkCompat(opts.dir, project);
    for (const i of compat.items) if (!i.ok) results.push(fail(`${i.label} ${i.version} is not supported (needs ${i.need})`));
    if (compat.ok) results.push(ok(`Compatible: ${describeCompat(compat.items)}`));
  }
  for (const [key, kind] of [
    [publicKey, "public"],
    [devKey, "developer"],
  ]) {
    if (!key) continue;
    const [good, bad, name] = KEY_CHECK[kind];
    const status = await checkKey(key, kind);
    results.push(status === "ok" ? ok(good) : status === "rejected" ? fail(bad) : warn(`Could not reach the LIKYLY API to check the ${name}`));
  }
  for (const editor of editors) {
    if (opts.dryRun || existsSync(configPath(opts.dir, editor))) results.push(ok(`${editorLabel(editor)} is connected to the LIKYLY MCP server`));
  }
  return results;
}
