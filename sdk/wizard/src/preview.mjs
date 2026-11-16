// The site's local preview, so the developer sees LIKYLY's changes as they are made:
// `shopify theme dev` for a Shopify theme, the project's own dev script otherwise.
//
// Run from a terminal, it takes over that terminal (the Shopify CLI may ask for the storefront
// password). Run by an AI assistant (no terminal to answer in), it opens a new terminal window
// instead: the prompt can be answered there, and the assistant's own session stays free.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { connect } from "node:net";
import { normalizeShopDomain } from "./shopify.mjs";
import { readDeveloperKey } from "./mcp.mjs";
import { API_URL, wizardUserAgent } from "./verify.mjs";

const readJson = (file) => {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return null;
  }
};

/** The store a theme previews against: --store, shopify.theme.toml, or the LIKYLY Shopify source. */
export async function themeStore(dir, { store, fetchImpl = fetch, env = process.env } = {}) {
  const given = normalizeShopDomain(store ?? env.SHOPIFY_FLAG_STORE ?? "");
  if (given) return given;
  const toml = join(dir, "shopify.theme.toml");
  if (existsSync(toml)) {
    const found = normalizeShopDomain(readFileSync(toml, "utf8").match(/^\s*store\s*=\s*"([^"]+)"/m)?.[1] ?? "");
    if (found) return found;
  }
  const apiKey = readDeveloperKey(dir, env);
  if (!apiKey) return null;
  try {
    const res = await fetchImpl(`${API_URL}/data-sources`, {
      headers: { "X-API-Key": apiKey, "User-Agent": wizardUserAgent("preview") },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    const source = (await res.json()).find((s) => s.type === "shopify" && s.config?.shop_domain);
    return normalizeShopDomain(source?.config?.shop_domain ?? "");
  } catch {
    return null;
  }
}

const runScript = (pm, script) => (pm === "npm" ? `npm run ${script}` : `${pm} ${script}`);

export const SHOPIFY_PREVIEW_PORT = 9292;

/** True when something already listens on 127.0.0.1:port. */
export function portTaken(port, host = "127.0.0.1") {
  return new Promise((done) => {
    const socket = connect({ port, host });
    const finish = (taken) => {
      socket.destroy();
      done(taken);
    };
    socket.setTimeout(1000, () => finish(false));
    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
  });
}

/** Is what answers on this port a Shopify storefront (a `shopify theme dev` already running)? */
export async function servesShopify(port, fetchImpl = fetch) {
  try {
    const res = await fetchImpl(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(5000) });
    return /cdn\.shopify\.com|Shopify\.theme/.test(await res.text());
  } catch {
    return false;
  }
}

/** The first free port after `from` (a few tried), or null. */
async function freePortAfter(from) {
  for (let port = from + 1; port <= from + 20; port++) if (!(await portTaken(port))) return port;
  return null;
}

/**
 * What runs the preview of this project, or null when the wizard knows none.
 * { command, requires, install, notes[], url?, running? } - `running` (the URL) when a preview
 * already answers there: nothing is to start, a second one would fail on the busy port.
 */
export async function previewPlan(project, dir, options = {}) {
  if (!project) return null;
  if (project.id === "shopify") {
    const store = await themeStore(dir, options);
    const wanted = Number(options.port) || SHOPIFY_PREVIEW_PORT;
    let port = wanted;
    const notes = [];
    if (await portTaken(wanted)) {
      if (await servesShopify(wanted, options.fetchImpl)) {
        const url = `http://127.0.0.1:${wanted}`;
        return { running: url, url, command: null, requires: null, notes: [`Open ${url}, then a product page: theme changes show up live.`] };
      }
      port = await freePortAfter(wanted);
      if (!port) return { blocked: `Ports ${wanted} to ${wanted + 20} are all taken: free one, or pass --port`, notes: [] };
      notes.push(`Port ${wanted} is taken by another program: the preview uses ${port}.`);
    }
    const url = `http://127.0.0.1:${port}`;
    const portFlag = port === SHOPIFY_PREVIEW_PORT ? "" : ` --port ${port}`;
    return {
      url,
      command: `shopify theme dev${store ? ` --store ${store}` : ""}${portFlag}`,
      requires: "shopify",
      install: "npm install -g @shopify/cli",
      notes: [
        ...notes,
        `Open the local address it prints (${url}), then a product page.`,
        `A password-protected store (every development store) asks for its storefront password: ${store ? `https://admin.shopify.com/store/${store.replace(/\.myshopify\.com$/, "")}/online_store/preferences` : "Shopify admin"} (Online Store → Preferences) → Store access → Private mode → Password. Not your admin login, not the app's client secret.`,
        `Nothing on ${url} means the preview did not start: read the error in its window.`,
        "Theme changes show up live while it runs.",
      ],
    };
  }
  if (project.id === "laravel") return { command: "php artisan serve", requires: "php", notes: ["Open http://127.0.0.1:8000."] };
  const scripts = readJson(join(dir, "package.json"))?.scripts ?? {};
  const script = scripts.dev ? "dev" : scripts.start ? "start" : null;
  if (!script) return null;
  const pm = project.packageManager ?? "npm";
  return { command: runScript(pm, script), requires: pm, notes: ["Open the local address it prints."] };
}

const shellQuote = (s) => `'${String(s).replace(/'/g, "'\\''")}'`;
const appleString = (s) => `"${String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;

/** Runs `command` in `dir` in a new terminal window. False when no terminal could be opened. */
export function openInNewTerminal(command, dir, { platform = process.platform, run = spawnSync, detach = spawn } = {}) {
  const banner = "LIKYLY preview - stop it with Ctrl+C";
  if (platform === "darwin") {
    const script = `cd ${shellQuote(dir)} && echo ${shellQuote(banner)} && ${command}`;
    const res = run("osascript", ["-e", `tell application "Terminal" to do script ${appleString(script)}`, "-e", 'tell application "Terminal" to activate'], { stdio: "ignore" });
    return res.status === 0;
  }
  if (platform === "win32") {
    try {
      detach("cmd", ["/c", "start", "LIKYLY preview", "cmd", "/k", `cd /d "${dir}" && ${command}`], { stdio: "ignore", detached: true, shell: false }).unref();
      return true;
    } catch {
      return false;
    }
  }
  const script = `cd ${shellQuote(dir)} && echo ${shellQuote(banner)} && ${command}; exec "\${SHELL:-bash}"`;
  for (const [bin, args] of [
    ["x-terminal-emulator", ["-e", "bash", "-lc", script]],
    ["gnome-terminal", ["--", "bash", "-lc", script]],
    ["konsole", ["-e", "bash", "-lc", script]],
    ["xterm", ["-e", "bash", "-lc", script]],
  ]) {
    if (run("which", [bin], { stdio: "ignore" }).status !== 0) continue;
    try {
      detach(bin, args, { stdio: "ignore", detached: true }).unref();
      return true;
    } catch {
      /* try the next one */
    }
  }
  return false;
}
