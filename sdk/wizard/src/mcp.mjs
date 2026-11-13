// Project-level MCP config for each AI coding tool. The developer key is never written into
// these files (they are usually committed): a tool that expands environment variables gets a
// reference to LIKYLY_DEVELOPER_KEY in its own syntax; the others get no key at all, and the
// LIKYLY server reads it from the environment or from the gitignored .likyly/credentials.env.

import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { EDITORS, editorById } from "./frameworks.mjs";

export const MCP_PACKAGE = "@likyly/mcp-server";
export const HOSTED_MCP_URL = "https://mcp.likyly.com/mcp";
export const KEY_ENV = "LIKYLY_DEVELOPER_KEY";
const STDIO = { command: "npx", args: ["-y", "-p", MCP_PACKAGE, "likyly-mcp-server"] };

/**
 * Testing against a local LIKYLY: LIKYLY_API_URL (also used by the verify step) becomes the
 * server's LIKYLY_BASE_URL, and LIKYLY_MCP_SERVER (a path to a built dist/index.js) replaces
 * the npm package. Unset, the config points at production through npx.
 */
function stdioCommand() {
  const local = process.env.LIKYLY_MCP_SERVER?.trim();
  return local ? { command: "node", args: [local] } : { ...STDIO, args: [...STDIO.args] };
}
function apiBaseUrl() {
  return (process.env.LIKYLY_API_URL?.trim() || "https://api.likyly.com").replace(/\/+$/, "");
}

export const CREDENTIALS_FILE = ".likyly/credentials.env";

function editorOf(id) {
  const editor = editorById(id);
  if (!editor) throw new Error(`unknown editor: ${id}`);
  return editor;
}

/**
 * The `likyly` server entry of a JSON-configured tool, local (stdio via npx) or hosted (HTTP,
 * only for tools that can reference the key from the environment).
 */
export function serverEntry(id, { hosted = false } = {}) {
  const editor = editorOf(id);
  if (editor.format !== "json") throw new Error(`${editor.label} is not configured in JSON`);
  const ref = editor.keyRef;
  if (hosted && ref) {
    const entry = { url: HOSTED_MCP_URL, headers: { Authorization: `Bearer ${ref}` } };
    return id === "cursor" ? entry : { type: "http", ...entry };
  }
  const env = {};
  if (ref) env.LIKYLY_API_KEY = ref;
  env.LIKYLY_BASE_URL = apiBaseUrl();
  // Claude Code and most others start the server in the project folder, where the relative
  // default resolves; Cursor and VS Code get the absolute path through ${workspaceFolder}.
  if (editor.workspaceVar) env.LIKYLY_ENV_FILE = `${editor.workspaceVar}/${CREDENTIALS_FILE}`;
  const entry = { ...stdioCommand(), env };
  return id === "vscode" ? { type: "stdio", ...entry } : entry;
}

// ---- TOML (Codex, Mistral Vibe): one block between markers, so it can be updated or removed
// without a TOML parser and without touching anything else in the file.

const BEGIN = "# >>> likyly";
const END = "# <<< likyly";
const toml = (value) => (Array.isArray(value) ? `[${value.map(toml).join(", ")}]` : JSON.stringify(value));

export function tomlBlock(id, { hosted = false } = {}) {
  const { command, args } = stdioCommand();
  const env = `{ LIKYLY_BASE_URL = ${toml(apiBaseUrl())} }`;
  const lines =
    id === "codex"
      ? hosted
        ? ["[mcp_servers.likyly]", `url = ${toml(HOSTED_MCP_URL)}`, `bearer_token_env_var = ${toml(KEY_ENV)}`]
        : ["[mcp_servers.likyly]", `command = ${toml(command)}`, `args = ${toml(args)}`, `env_vars = ${toml([KEY_ENV])}`, `env = ${env}`]
      : ["[[mcp_servers]]", 'name = "likyly"', 'transport = "stdio"', `command = ${toml(command)}`, `args = ${toml(args)}`, `env = ${env}`];
  return [`${BEGIN} (written by @likyly/wizard; the developer key stays in ${CREDENTIALS_FILE} or your environment)`, ...lines, END].join("\n");
}

const blockPattern = () => new RegExp(`\\n*${BEGIN}[^\\n]*\\n[\\s\\S]*?${END}[^\\n]*\\n?`);
/** A likyly server the developer defined by hand (outside our markers): left alone. */
const FOREIGN = { "codex-toml": /^\s*\[mcp_servers\.likyly\]/m, "vibe-toml": /^\s*name\s*=\s*["']likyly["']/m };

export function hasTomlBlock(raw) {
  return blockPattern().test(raw);
}

export function removeTomlBlock(raw) {
  const rest = raw.replace(blockPattern(), "\n").replace(/\n{3,}/g, "\n\n");
  return rest.trim() ? rest.replace(/^\n+/, "") : "";
}

function mergeToml(editor, raw, opts) {
  const block = tomlBlock(editor.id, opts);
  if (hasTomlBlock(raw)) return { text: raw.replace(blockPattern(), (m) => (m.startsWith("\n") ? "\n\n" : "") + block + "\n"), replaced: true };
  if (FOREIGN[editor.format].test(raw)) throw new Error(`${editor.file} already defines a likyly server: update it by hand`);
  const sep = !raw.trim() ? "" : raw.endsWith("\n\n") ? "" : raw.endsWith("\n") ? "\n" : "\n\n";
  return { text: (raw.trim() ? raw : "") + sep + block + "\n", replaced: false };
}

// ---- JSON

// Earlier wizard versions asked VS Code to prompt for the key; that input is removed on update.
const LEGACY_VSCODE_INPUT = "likyly-developer-key";

/**
 * Merge the `likyly` server into an existing JSON config object (other servers untouched).
 * Returns `{ config, replaced }`.
 */
export function mergeConfig(id, existing, opts) {
  const editor = editorOf(id);
  const config = existing && typeof existing === "object" ? structuredClone(existing) : {};
  const key = editor.serversKey;
  config[key] = config[key] && typeof config[key] === "object" ? config[key] : {};
  const replaced = Boolean(config[key].likyly);
  config[key].likyly = serverEntry(id, opts);
  if (id === "vscode" && Array.isArray(config.inputs)) {
    config.inputs = config.inputs.filter((i) => i?.id !== LEGACY_VSCODE_INPUT);
    if (!config.inputs.length) delete config.inputs;
  }
  return { config, replaced };
}

export function configPath(dir, id) {
  return join(dir, editorOf(id).file);
}

/** Write (or update) one tool's MCP config. Throws when the existing file cannot be merged safely. */
export function writeMcpConfig(dir, id, { hosted = false, dryRun = false } = {}) {
  const editor = editorOf(id);
  const path = configPath(dir, id);
  const raw = existsSync(path) ? readFileSync(path, "utf8") : "";
  let text, replaced, config;
  if (editor.format === "json") {
    let existing = null;
    try {
      existing = raw.trim() ? JSON.parse(raw) : null;
    } catch {
      throw new Error(`${path} is not valid JSON (comments?): add the likyly server by hand`);
    }
    ({ config, replaced } = mergeConfig(id, existing, { hosted }));
    text = JSON.stringify(config, null, 2) + "\n";
  } else {
    ({ text, replaced } = mergeToml(editor, raw, { hosted }));
  }
  if (!dryRun) {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, text);
  }
  return { path, replaced, config, text };
}

/** Whether this tool's config holds the likyly server (JSON key, or our TOML block). */
export function hasLikylyServer(dir, id) {
  const editor = editorOf(id);
  const path = configPath(dir, id);
  if (!existsSync(path)) return false;
  const raw = readFileSync(path, "utf8");
  if (editor.format !== "json") return hasTomlBlock(raw);
  try {
    return Boolean(JSON.parse(raw)?.[editor.serversKey]?.likyly);
  } catch {
    return false;
  }
}

/**
 * The uninstall step for one tool's config: `null` when there is nothing of ours, `{ kept }` when
 * the file cannot be edited safely, else `{ label, run }`. A file left with nothing else goes too.
 */
export function removalStep(dir, id, rel) {
  const editor = editorOf(id);
  const path = configPath(dir, id);
  if (!existsSync(path)) return null;
  const raw = readFileSync(path, "utf8");
  if (editor.format !== "json") {
    if (!hasTomlBlock(raw)) return null;
    const rest = removeTomlBlock(raw);
    return {
      label: rest ? `Remove the likyly server from ${rel}` : `Delete ${rel} (only LIKYLY was in it)`,
      run: () => (rest ? writeFileSync(path, rest) : unlinkSync(path)),
    };
  }
  let config;
  try {
    config = JSON.parse(raw);
  } catch {
    return { kept: `${rel} (not valid JSON: remove the "likyly" server by hand)` };
  }
  const key = editor.serversKey;
  if (!config?.[key]?.likyly) return null;
  delete config[key].likyly;
  const empty = !Object.keys(config[key]).length && Object.keys(config).every((k) => k === key || (k === "inputs" && !config.inputs?.length));
  return {
    label: empty ? `Delete ${rel} (only LIKYLY was in it)` : `Remove the likyly server from ${rel}`,
    run: () => (empty ? unlinkSync(path) : writeFileSync(path, JSON.stringify(config, null, 2) + "\n")),
  };
}

/** Every folder a supported tool reads project skills from (the LIKYLY skill goes in `<dir>/likyly`). */
export const SKILL_DIRS = [...new Set(EDITORS.map((e) => e.skillDir))];

/** A developer key the LIKYLY server will find: exported, or in this project's credentials file. */
export function hasDeveloperKey(dir, env = process.env) {
  if (env[KEY_ENV]?.trim() || env.LIKYLY_API_KEY?.trim()) return true;
  const path = join(dir, CREDENTIALS_FILE);
  return existsSync(path) && /^\s*(LIKYLY_DEVELOPER_KEY|LIKYLY_API_KEY)\s*=\s*\S/m.test(readFileSync(path, "utf8"));
}

// Claude Code asks before starting a server found in .mcp.json, with "don't use it" selected. The
// developer already chose Claude Code in the wizard: that approval goes in the project's personal
// settings (.claude/settings.local.json, not meant to be committed).
export const CLAUDE_LOCAL_SETTINGS = ".claude/settings.local.json";

/** Add "likyly" to enabledMcpjsonServers. Returns { path, status: "added" | "same" | "invalid" }. */
export function approveClaudeServer(dir, { dryRun = false } = {}) {
  const path = join(dir, CLAUDE_LOCAL_SETTINGS);
  let settings = {};
  if (existsSync(path)) {
    try {
      settings = JSON.parse(readFileSync(path, "utf8") || "{}");
    } catch {
      return { path, status: "invalid" };
    }
  }
  const list = Array.isArray(settings.enabledMcpjsonServers) ? settings.enabledMcpjsonServers : [];
  if (list.includes("likyly")) return { path, status: "same" };
  settings.enabledMcpjsonServers = [...list, "likyly"];
  if (!dryRun) {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(settings, null, 2) + "\n");
  }
  return { path, status: "added" };
}

/** Uninstall: take "likyly" out of that list; a file left with nothing else goes too. */
export function unapproveStep(dir) {
  const path = join(dir, CLAUDE_LOCAL_SETTINGS);
  if (!existsSync(path)) return null;
  let settings;
  try {
    settings = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
  if (!settings?.enabledMcpjsonServers?.includes?.("likyly")) return null;
  settings.enabledMcpjsonServers = settings.enabledMcpjsonServers.filter((s) => s !== "likyly");
  if (!settings.enabledMcpjsonServers.length) delete settings.enabledMcpjsonServers;
  const empty = !Object.keys(settings).length;
  return {
    label: empty ? `Delete ${CLAUDE_LOCAL_SETTINGS} (only the LIKYLY approval was in it)` : `Remove the likyly approval from ${CLAUDE_LOCAL_SETTINGS}`,
    run: () => (empty ? unlinkSync(path) : writeFileSync(path, JSON.stringify(settings, null, 2) + "\n")),
  };
}
