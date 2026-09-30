#!/usr/bin/env node
// Generates, from ONE source, everything that documents the SDKs:
//   - sdk/<lang>/README.md                       (from docs/README.template.md + docs/sdks.json)
//   - sdk/docs/METHODS.md                        (the method matrix)
//   - <website>/src/content/sdk-docs.generated.json  (the data behind /docs/sdk/<lang>)
// Every code sample is cut out of <lang>/examples/quickstart.* between `region:` / `endregion` markers, and
// conformance/run-examples.sh executes those files against a real API - so the documentation cannot drift from
// code that works. Lines marked `docs:omit` (test plumbing such as the base URL) are dropped from the samples.
//
//   node docs/generate.mjs            write everything
//   node docs/generate.mjs --check    fail if anything on disk is stale (for CI)
//   node docs/generate.mjs --website ../../likyly-website
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const sdkRoot = resolve(here, "..");
const args = process.argv.slice(2);
const check = args.includes("--check");
const websiteArg = args.includes("--website") ? args[args.indexOf("--website") + 1] : join(sdkRoot, "../../likyly-website");
const websiteRoot = existsSync(websiteArg) ? resolve(websiteArg) : null;

const meta = JSON.parse(readFileSync(join(here, "sdks.json"), "utf8"));
const template = readFileSync(join(here, "README.template.md"), "utf8");

// ---- snippets -------------------------------------------------------------------------------------

function regions(source) {
  const out = {};
  let name = null;
  for (const line of source.split("\n")) {
    const start = line.match(/^\s*(?:\/\/|#)\s*region:(\w+)\s*$/);
    if (start) { name = start[1]; out[name] = []; continue; }
    if (/^\s*(?:\/\/|#)\s*endregion\s*$/.test(line)) { name = null; continue; }
    if (name) out[name].push(line);
  }
  return out;
}

function clean(lines, fence) {
  let kept = lines.filter((l) => !l.includes("docs:omit"));
  const indent = Math.min(...kept.filter((l) => l.trim()).map((l) => l.match(/^[\t ]*/)[0].length));
  kept = kept.map((l) => l.slice(Math.min(indent, l.match(/^[\t ]*/)[0].length)));
  let text = kept.join("\n").replace(/^\n+|\s+$/g, "");
  // an argument list that lost its last (omitted) argument must not end with a dangling comma
  if (fence === "go") text = text.replace(/,\n\s*\)/g, ")");
  if (fence === "ruby") text = text.replace(/,\n(\s*)\)/g, "\n$1)");
  return text;
}

function snippetsOf(sdk) {
  const file = join(sdkRoot, sdk.dir, sdk.example);
  const r = regions(readFileSync(file, "utf8"));
  for (const need of ["imports", "initialize", "catalog", "track", "recommend", "attribution", "showcase", "browser", "custom", "advanced", "errors", "config"]) {
    if (!r[need]) throw new Error(`${sdk.id}: region "${need}" missing in ${sdk.dir}/${sdk.example}`);
  }
  const get = (n) => clean(r[n], sdk.fence);
  const init = [sdk.initPrelude?.trimEnd(), get("imports"), get("initialize")].filter(Boolean).join("\n\n");
  return {
    init, initialize: get("initialize"), catalog: get("catalog"), track: get("track"), recommend: get("recommend"), attribution: get("attribution"), showcase: get("showcase"),
    browser: get("browser"), custom: get("custom"), advanced: get("advanced"), errors: get("errors"), config: get("config"),
  };
}

// ---- method names ---------------------------------------------------------------------------------

const OPS = [
  ["items", "get", "One item by your own id", "S"],
  ["items", "list", "One page of the catalog (`limit` / `offset`), with the catalog's total", "S"],
  ["items", "upsert", "Create or replace an item (idempotent)", "S"],
  ["items", "delete", "Remove an item (events already recorded for it are kept)", "S"],
  ["items", "upsertMany", "Batch upsert, 1 to 1000 items", "S"],
  ["items", "import", "Alias of the batch upsert above (JSON batch)", "S"],
  ["items", "deleteMany", "Batch delete, 1 to 1000 ids; unknown ids are reported, not raised", "S"],
  ["users", "get", "One user profile", "S"],
  ["users", "list", "One page of users (`limit` / `offset`)", "S"],
  ["users", "upsert", "Create or replace a profile (free-form `properties`)", "S"],
  ["users", "delete", "Erase the profile **and every event recorded for that user**", "S"],
  ["users", "import", "Batch upsert of users, 1 to 1000", "S"],
  ["events", "track", "Record an event of any type (open string)", "P"],
  ["events", "trackMany", "Up to 1000 events in one call, each with its own type", "P"],
  ["events", "impression", "The item was displayed to the visitor", "P"],
  ["events", "view", "The visitor looked at the item", "P"],
  ["events", "click", "The visitor clicked the item", "P"],
  ["events", "addToCart", "The visitor added the item to their cart", "P"],
  ["events", "removeFromCart", "The visitor removed the item from their cart", "P"],
  ["events", "purchase", "The visitor bought the item (set an event id)", "P"],
  ["recommendations", "get", "Recommendations with the automatic strategy: user, session, item or viewed items", "P"],
  ["recommendations", "popular", "Advanced: the most popular items", "P"],
  ["recommendations", "similar", "Advanced: items similar to one item", "P"],
  ["recommendations", "collaborative", "Advanced: what similar users liked (needs a trained model)", "P"],
  ["recommendations", "hybrid", "Advanced: similar items personalised for a user (`alpha`)", "P"],
  ["recommendations", "session", "Advanced: from viewed items or a user's view history", "P"],
];
const ABSENT = [
  ["users", "upsertMany", "no batch-upsert endpoint for users (use `users.import`)"],
  ["users", "deleteMany", "no batch-delete endpoint for users"],
];

const snake = (s) => s.replace(/[A-Z]/g, (c) => "_" + c.toLowerCase());
const pascal = (s) => s[0].toUpperCase() + s.slice(1);
const CALL = {
  typescript: (r, m) => `likyly.${r}.${m}`,
  php: (r, m) => `$likyly->${r}()->${m}`,
  python: (r, m) => `likyly.${r}.${m}`,
  java: (r, m) => `likyly.${r}().${m}`,
  dotnet: (r, m) => `likyly.${pascal(r)}.${m}`,
  go: (r, m) => `client.${pascal(r)}.${m}`,
  ruby: (r, m) => `likyly.${r}.${m}`,
  rust: (r, m) => `likyly.${r}().${m}`,
};

function methodName(sdk, resource, op) {
  const special = sdk.specialNames[`${resource}.${op}`];
  if (special) return special;
  const base = sdk.case === "snake" ? snake(op) : sdk.case === "pascal" ? pascal(op) : op;
  return base + (sdk.suffix ?? "");
}
const callOf = (sdk, resource, op) => CALL[sdk.id](resource, methodName(sdk, resource, op));

// ---- rendering --------------------------------------------------------------------------------------

function render(sdk, snippets) {
  const rows = OPS.map(([r, op, what, key]) => `| \`${callOf(sdk, r, op)}\` | ${what} | ${key} |`).join("\n");
  const fill = {
    name: sdk.name, short: sdk.short, slug: sdk.slug, package: sdk.package, registry: sdk.registry, fence: sdk.fence,
    install: sdk.install, installFence: sdk.installFence, requires: sdk.requires, context: sdk.context,
    perCall: sdk.perCall, errFields: sdk.errors.fields,
    configRows: sdk.config.map(([a, b, c]) => `| ${a} | ${b} | ${c} |`).join("\n"),
    methodRows: rows,
    notes: sdk.notes.map((n) => `- ${n}`).join("\n"),
  };
  return template
    .replace(/\{\{snippet:(\w+)\}\}/g, (_, k) => snippets[k])
    .replace(/\{\{err:(\w+)\}\}/g, (_, k) => sdk.errors[k])
    .replace(/\{\{(\w+)\}\}/g, (_, k) => {
      if (!(k in fill)) throw new Error(`unknown placeholder {{${k}}}`);
      return fill[k];
    });
}

function methodsMatrix() {
  const header = `| Operation | ${meta.sdks.map((s) => s.short).join(" | ")} | Key |\n|---|${meta.sdks.map(() => "---").join("|")}|---|`;
  const rows = OPS.map(([r, op, , key]) => `| ${r}.${op} | ${meta.sdks.map((s) => `\`${methodName(s, r, op)}\``).join(" | ")} | ${key} |`);
  const absent = ABSENT.map(([r, op, why]) => `| ${r}.${op} | ${meta.sdks.map(() => "—").join(" | ")} | (${why}) |`);
  return `# SDK method matrix

Generated by \`docs/generate.mjs\` - do not edit. Version ${meta.version}. Key: **S** = secret key only, **P** = public or secret key.
Names follow each language's conventions; \`import\` is a reserved word in Python and Java, hence \`import_\` / \`importItems\` / \`importUsers\`.
.NET methods carry the \`Async\` suffix and take a \`CancellationToken\`.

${[header, ...rows, ...absent].join("\n")}
`;
}

// ---- outputs ----------------------------------------------------------------------------------------

const outputs = new Map();
const website = { version: meta.version, operations: OPS.map(([resource, op, what, key]) => ({ resource, op, what, key })), sdks: [] };
for (const sdk of meta.sdks) {
  const snippets = snippetsOf(sdk);
  outputs.set(join(sdkRoot, sdk.dir, "README.md"), render(sdk, snippets));
  website.sdks.push({
    ...sdk,
    snippets,
    methods: OPS.map(([resource, op, what, key]) => ({ resource, op, call: callOf(sdk, resource, op), what, key })),
  });
}
outputs.set(join(here, "METHODS.md"), methodsMatrix());
if (websiteRoot) outputs.set(join(websiteRoot, "src/content/sdk-docs.generated.json"), JSON.stringify(website, null, 2) + "\n");

let stale = 0;
for (const [path, content] of outputs) {
  const current = existsSync(path) ? readFileSync(path, "utf8") : null;
  if (check) {
    if (current !== content) { console.error(`stale: ${path}`); stale++; }
  } else {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content);
    console.log(`wrote ${path.replace(sdkRoot + "/", "sdk/")}`);
  }
}
if (check) process.exit(stale ? 1 : 0);
