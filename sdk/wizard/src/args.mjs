import { resolve } from "node:path";
import { EDITORS } from "./frameworks.mjs";
import { isServerOnlyKey } from "./setup.mjs";
import { SERVER_KEY_MESSAGE } from "./constants.mjs";

const color = !process.env.NO_COLOR && process.stdout.isTTY;
const hex = (h) => (s) => {
  if (!color) return String(s);
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  return `\x1b[38;2;${r};${g};${b}m${s}\x1b[39m`;
};
const bold = (s) => (color ? `\x1b[1m${s}\x1b[22m` : String(s));
const blue = hex("#2F6BFF");
const dim = (s) => (color ? `\x1b[2m${s}\x1b[22m` : String(s));

export const HELP = `
${bold("LIKYLY wizard")} ${dim("· recommendations on your site, installed for you")}

${blue("Usage")}
  npx @likyly/wizard [command] [options]

${blue("Commands")}
  install        ${dim("(default)")} detect the framework, install the SDK, write your key,
                 connect your AI assistant to the LIKYLY MCP server
  mcp add        only connect your AI assistant (signs you in first if needed)
  shopify        connect a Shopify store's catalog: you type the app's credentials here,
                 they go straight to LIKYLY (encrypted), never to your assistant
  magento        connect a Magento / Adobe Commerce store's catalog, the same way: the
                 store's address and an Integration's four values, typed here
  preview        start the site's local preview (shopify theme dev, or the dev script) right
                 here, in this terminal (Ctrl+C stops it); already running: just its address.
                 From an assistant (no terminal): the command to run in yours
  detect         print what was detected, change nothing
  inventory      list what LIKYLY put in this project: packages, key variable, setup files,
                 and every source line that uses LIKYLY (read-only; --json for data)
  pages          where to see LIKYLY: one example URL per kind of page with a recommendation
                 block (a product, a category, the cart...), locally and in production
                 (uses your developer key; --json for data)
  pixel          Shopify theme: the pixel to paste in the admin (Settings → Customer events)
                 so add to cart and purchases reach LIKYLY with the recommendation they came
                 from - the revenue in impact and in the console; copied to the clipboard
  impact [days]  what the recommendations bring: served, seen, clicked, the purchases and
                 revenue they led to (uses your developer key; Free: 7 days, Pro: up to 90
                 days with the daily trend and the top products; --json for data)
  cache clear [placement]
                 empty LIKYLY's recommendation cache: one placement, or all of them
                 (uses your developer key from .likyly/credentials.env)
  uninstall      remove what the wizard added (SDK, key, MCP config, skill, commands),
                 after showing the plan; your code and your LIKYLY account are not touched
  help           this help

${blue("Options")}
  --dir <path>           project folder ${dim("(default: current folder)")}
  --public-key <key>     your LIKYLY public key ${dim("(asked otherwise)")}
  --editor <ids>         ${EDITORS.map((e) => e.id).join(",")} ${dim("(default: the detected ones)")}
  --placement <slug>     with cache clear: the placement to empty (default: all)
  --branch <new|current> with uninstall, in a git repository: work on a new branch
                         likyly/uninstall, or on the current one (asked otherwise)
  --json                 with inventory, pages or impact: print JSON
  --days <n>             with impact: how many days back ${dim("(capped by your plan)")}
  --store <shop>         with preview: the Shopify store ${dim("(default: shopify.theme.toml, or your LIKYLY source)")}
  --new-window           with preview: run it in a new terminal window instead of this one
  --port <n>             with preview: the local port ${dim("(default: 9292 for a Shopify theme, the next free one if taken)")}
  --hosted               use the hosted MCP server (mcp.likyly.com) instead of npx
  --no-install           do not run the package manager
  --dry-run              show what would happen, write nothing
  -y, --yes              accept the defaults, ask nothing ${dim("(CI)")}
  -h, --help             this help
  -v, --version          version
`;

export function parseArgs(argv) {
  const opts = { command: "install", dir: process.cwd(), yes: false, dryRun: false, install: true, hosted: false, editors: null, publicKey: null, placement: null, branch: null, json: false, store: null, newWindow: false, port: null, days: null };
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${a} expects a value`);
      return v;
    };
    if (a === "-h" || a === "--help") opts.command = "help";
    else if (a === "-v" || a === "--version") opts.command = "version";
    else if (a === "-y" || a === "--yes") opts.yes = true;
    else if (a === "--dry-run") opts.dryRun = true;
    else if (a === "--no-install") opts.install = false;
    else if (a === "--hosted") opts.hosted = true;
    else if (a === "--json") opts.json = true;
    else if (a === "--here") opts.newWindow = false; // the default since 0.14.2, still accepted
    else if (a === "--days") opts.days = next();
    else if (a === "--port") {
      opts.port = Number(next());
      if (!Number.isInteger(opts.port) || opts.port < 1 || opts.port > 65535) throw new Error("--port expects a port number");
    }
    else if (a === "--new-window") opts.newWindow = true;
    else if (a === "--store") opts.store = next();
    else if (a === "--dir") opts.dir = next();
    else if (a === "--public-key") opts.publicKey = next();
    else if (a === "--placement") opts.placement = next();
    else if (a === "--branch") {
      opts.branch = next();
      if (!["new", "current"].includes(opts.branch)) throw new Error(`--branch expects new or current, got: ${opts.branch}`);
    }
    else if (a === "--editor" || a === "--editors") opts.editors = next().split(",").map((s) => s.trim()).filter(Boolean);
    else if (a.startsWith("-")) throw new Error(`unknown option: ${a}`);
    else positional.push(a);
  }
  if (opts.command !== "help" && opts.command !== "version" && positional.length) {
    const cmd = positional.join(" ");
    if (positional[0] === "cache") {
      // cache clear [placement]  (purge is accepted as a synonym)
      if (!["clear", "purge"].includes(positional[1] ?? "")) throw new Error(`unknown command: ${cmd} (expected: cache clear [placement])`);
      if (positional.length > 3) throw new Error(`cache clear takes one placement at most, got: ${positional.slice(2).join(" ")}`);
      opts.command = "cache-clear";
      opts.placement = positional[2] ?? opts.placement;
    } else if (positional[0] === "impact") {
      // impact [days]
      if (positional.length > 2) throw new Error(`impact takes a number of days at most, got: ${positional.slice(1).join(" ")}`);
      opts.command = "impact";
      opts.days = positional[1] ?? opts.days;
    } else if (cmd === "mcp add" || cmd === "mcp") opts.command = "mcp";
    else if (cmd === "shopify") opts.command = "shopify";
    else if (cmd === "magento") opts.command = "magento";
    else if (cmd === "preview") opts.command = "preview";
    else if (cmd === "pages") opts.command = "pages";
    else if (cmd === "pixel") opts.command = "pixel";
    else if (cmd === "detect") opts.command = "detect";
    else if (cmd === "inventory") opts.command = "inventory";
    else if (cmd === "uninstall") opts.command = "uninstall";
    else if (cmd === "install") opts.command = "install";
    else if (cmd === "help") opts.command = "help";
    else throw new Error(`unknown command: ${cmd}`);
  }
  if (opts.days !== null && opts.days !== undefined && !(Number.isInteger(Number(opts.days)) && Number(opts.days) > 0)) throw new Error(`days expects a whole number of days, got: ${opts.days}`);
  opts.dir = resolve(opts.dir);
  const known = EDITORS.map((e) => e.id);
  for (const e of opts.editors ?? []) if (!known.includes(e)) throw new Error(`unknown editor: ${e} (expected: ${known.join(", ")})`);
  // Refused up front, before anything is installed or written.
  if (opts.publicKey && isServerOnlyKey(opts.publicKey)) throw new Error(`--public-key: ${SERVER_KEY_MESSAGE}`);
  return opts;
}
