// Project-level MCP config for each AI coding tool. The developer key is never written into
// these files (they are usually committed): each one references an environment variable or
// prompts for it, in the syntax that tool understands.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

export const MCP_PACKAGE = "@likyly/mcp-server";
export const HOSTED_MCP_URL = "https://mcp.likyly.com/mcp";
export const KEY_ENV = "LIKYLY_DEVELOPER_KEY";
const STDIO = { command: "npx", args: ["-y", "-p", MCP_PACKAGE, "likyly-mcp-server"] };

/** The `likyly` server entry for one editor, local (stdio via npx) or hosted (HTTP). */
export function serverEntry(editor, { hosted = false } = {}) {
  const ref = {
    "claude-code": `\${${KEY_ENV}}`,
    cursor: `\${env:${KEY_ENV}}`,
    vscode: "${input:likyly-developer-key}",
  }[editor];
  if (!ref) throw new Error(`unknown editor: ${editor}`);

  if (hosted) {
    const entry = { url: HOSTED_MCP_URL, headers: { Authorization: `Bearer ${ref}` } };
    return editor === "cursor" ? entry : { type: "http", ...entry };
  }
  const entry = { ...STDIO, args: [...STDIO.args], env: { LIKYLY_API_KEY: ref, LIKYLY_BASE_URL: "https://api.likyly.com" } };
  return editor === "vscode" ? { type: "stdio", ...entry } : entry;
}

const VSCODE_INPUT = {
  type: "promptString",
  id: "likyly-developer-key",
  description: "Clé développeur LIKYLY (www.likyly.com/account/keys)",
  password: true,
};

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
  if (editor === "vscode") {
    const inputs = Array.isArray(config.inputs) ? config.inputs.filter((i) => i?.id !== VSCODE_INPUT.id) : [];
    config.inputs = [...inputs, VSCODE_INPUT];
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
      throw new Error(`${path} n'est pas du JSON valide (commentaires ?) : ajoutez le serveur à la main`);
    }
  }
  const { config, replaced } = mergeConfig(editor, existing, { hosted });
  if (!dryRun) {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(config, null, 2) + "\n");
  }
  return { path, replaced, config };
}
