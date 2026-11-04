// `npx @likyly/wizard` - detect the framework, install the SDK, write the public key, connect
// the developer's AI coding tool to the LIKYLY MCP server, and hand over to it with a prompt.
//
// In a terminal it runs the Ink UI (src/ui); with --yes, in CI or through a pipe it prints
// plain lines (src/plain.mjs). Both run the same steps (src/actions.mjs).

import { readFileSync } from "node:fs";
import { detectEditors, detectProject } from "./frameworks.mjs";
import { HELP, parseArgs } from "./args.mjs";
import { runPlain } from "./plain.mjs";

export { parseArgs };

export async function main(argv = process.argv.slice(2)) {
  let opts;
  try {
    opts = parseArgs(argv);
  } catch (err) {
    console.error(`Error: ${err.message}\n${HELP}`);
    return 2;
  }
  if (opts.command === "help") {
    console.log(HELP);
    return 0;
  }
  const version = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).version;
  if (opts.command === "version") {
    console.log(version);
    return 0;
  }
  if (opts.command === "detect") {
    console.log(JSON.stringify({ project: detectProject(opts.dir), editors: detectEditors(opts.dir) }, null, 2));
    return 0;
  }
  const interactive = !opts.yes && process.stdin.isTTY && process.stdout.isTTY && !process.env.CI;
  if (!interactive) return runPlain(opts);
  const { runInteractive } = await import("./ui/app.jsx");
  return runInteractive(opts, version);
}
