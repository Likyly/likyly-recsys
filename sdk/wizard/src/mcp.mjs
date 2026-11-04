// Project-level MCP config for each AI coding tool. The developer key is never written into
// these files (they are usually committed): each one references an environment variable or
// prompts for it, in the syntax that tool understands.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

export const MCP_PACKAGE = "@likyly/mcp-server";
export const HOSTED_MCP_URL = "https://mcp.likyly.com/mcp";
export const KEY_ENV = "LIKYLY_DEVELOPER_KEY";
const STDIO = { command: "npx", args: ["-y", "-p", MCP_PACKAGE, "likyly-mcp-server"] };

export const CREDENTIALS_FILE = ".likyly/credentials.env";

/**
 * The `likyly` server entry for one editor, local (stdio via npx) or hosted (HTTP). The key is
 * never in the file: the server reads LIKYLY_DEVELOPER_KEY from the environment when it is
 * exported, otherwise from the gitignored credentials file the wizard writes.
 */
export function serverEntry(editor, { hosted = false } = {}) {
  const ref = {
    "claude-code": `\${${KEY_ENV}:-}`,
    cursor: `\${env:${KEY_ENV}}`,
    vscode: `\${env:${KEY_ENV}}`,
  }[editor];
  if (!ref) throw new Error(`unknown editor: ${editor}`);

  if (hosted) {
    const entry = { url: HOSTED_MCP_URL, headers: { Authorization: `Bearer ${ref}` } };
    return editor === "cursor" ? entry : { type: "http", ...entry };
  }
  const env = { LIKYLY_API_KEY: ref, LIKYLY_BASE_URL: "https://api.likyly.com" };
  // Claude Code starts the server in the project folder, where the relative default resolves;
  // Cursor and VS Code get the absolute path through their ${workspaceFolder} variable.
  if (editor !== "claude-code") env.LIKYLY_ENV_FILE = `\${workspaceFolder}/${CREDENTIALS_FILE}`;
  const entry = { ...STDIO, args: [...STDIO.args], env };
  return editor === "vscode" ? { type: "stdio", ...entry } : entry;
}

// Earlier wizard versions asked VS Code to prompt for the key; that input is removed on update.
const LEGACY_VSCODE_INPUT = "likyly-developer-key";

/**
 * Merge the `likyly` server into an existing config object (other servers untouched).
 * Returns `{ config, replaced }`.
 */
export function mergeConfig(editor, existing, opts) {
  const config = existing && typeof existing === "object" ? structuredClone(existing) : {};
  const key = editor === "vscode" ? "servers" : "mcpServers";
  config[key] = config[key] && typeof config[key] === "object" ? config[key] : {};
  const replaced = Boolean(config[key].likyly);
  config[key].likyly = serverEntry(editor, opts);
  if (editor === "vscode" && Array.isArray(config.inputs)) {
    config.inputs = config.inputs.filter((i) => i?.id !== LEGACY_VSCODE_INPUT);
    if (!config.inputs.length) delete config.inputs;
  }
  return { config, replaced };
}

export function configPath(dir, editor) {
  const rel = { "claude-code": ".mcp.json", cursor: ".cursor/mcp.json", vscode: ".vscode/mcp.json" }[editor];
  return join(dir, rel);
}

/** Write (or update) one editor's MCP config. Throws when the existing file is not valid JSON. */
export function writeMcpConfig(dir, editor, { hosted = false, dryRun = false } = {}) {
  const path = configPath(dir, editor);
  let existing = null;
  if (existsSync(path)) {
    const raw = readFileSync(path, "utf8");
    try {
      existing = raw.trim() ? JSON.parse(raw) : null;
    } catch {
      throw new Error(`${path} is not valid JSON (comments?): add the likyly server by hand`);
    }
  }
  const { config, replaced } = mergeConfig(editor, existing, { hosted });
  if (!dryRun) {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(config, null, 2) + "\n");
  }
  return { path, replaced, config };
}
