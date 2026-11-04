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
  mcp add        only connect your AI assistant (Claude Code, Cursor, VS Code)
  detect         print what was detected, change nothing
  cache clear [placement]
                 empty LIKYLY's recommendation cache: one placement, or all of them
                 (uses your developer key from .likyly/credentials.env)
  uninstall      remove what the wizard added (SDK, key, MCP config, skill, commands),
                 after showing the plan; your code and your LIKYLY account are not touched

${blue("Options")}
  --dir <path>           project folder ${dim("(default: current folder)")}
  --public-key <key>     your LIKYLY public key ${dim("(asked otherwise)")}
  --editor <ids>         claude-code,cursor,vscode ${dim("(default: the detected ones)")}
  --placement <slug>     with cache clear: the placement to empty (default: all)
  --hosted               use the hosted MCP server (mcp.likyly.com) instead of npx
  --no-install           do not run the package manager
  --dry-run              show what would happen, write nothing
  -y, --yes              accept the defaults, ask nothing ${dim("(CI)")}
  -h, --help             this help
  -v, --version          version
`;

export function parseArgs(argv) {
  const opts = { command: "install", dir: process.cwd(), yes: false, dryRun: false, install: true, hosted: false, editors: null, publicKey: null, placement: null };
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
    else if (a === "--dir") opts.dir = next();
    else if (a === "--public-key") opts.publicKey = next();
    else if (a === "--placement") opts.placement = next();
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
    } else if (cmd === "mcp add" || cmd === "mcp") opts.command = "mcp";
    else if (cmd === "detect") opts.command = "detect";
    else if (cmd === "uninstall") opts.command = "uninstall";
    else if (cmd === "install") opts.command = "install";
    else throw new Error(`unknown command: ${cmd}`);
  }
  opts.dir = resolve(opts.dir);
  const known = EDITORS.map((e) => e.id);
  for (const e of opts.editors ?? []) if (!known.includes(e)) throw new Error(`unknown editor: ${e} (expected: ${known.join(", ")})`);
  // Refused up front, before anything is installed or written.
  if (opts.publicKey && isServerOnlyKey(opts.publicKey)) throw new Error(`--public-key: ${SERVER_KEY_MESSAGE}`);
  return opts;
}
