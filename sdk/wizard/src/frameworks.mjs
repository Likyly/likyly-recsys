// Framework detection: look at the project's own manifest files (never guess from the
// folder name) and return everything the later steps need - which SDK packages to add, how to
// add them, which env file and variable name the framework exposes to the browser, and which
// starter file (if any) to write.

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const JS_SDK = "@likyly/sdk";
const REACT_SDK = "@likyly/react";

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

function readText(path) {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return "";
  }
}

function has(dir, ...names) {
  return names.some((n) => existsSync(join(dir, n)));
}

/** The JS package manager this project already uses, from its lockfile (npm when none). */
export function detectJsPackageManager(dir) {
  if (has(dir, "bun.lockb", "bun.lock")) return "bun";
  if (has(dir, "pnpm-lock.yaml")) return "pnpm";
  if (has(dir, "yarn.lock")) return "yarn";
  return "npm";
}

function jsAddCommand(pm, packages) {
  const verb = pm === "npm" ? "install" : "add";
  return [pm, verb, ...packages];
}

function pythonAddCommand(dir) {
  if (has(dir, "uv.lock")) return { cmd: ["uv", "add", "likyly"], run: true };
  if (has(dir, "poetry.lock")) return { cmd: ["poetry", "add", "likyly"], run: true };
  // A bare `pip install` could land in the wrong interpreter (no venv active, system Python...):
  // print it instead of running it.
  return { cmd: ["pip", "install", "likyly"], run: false };
}

function srcPrefix(dir) {
  return existsSync(join(dir, "src")) ? "src/" : "";
}

// Ordered: the first matching rule wins, so meta-frameworks come before the libraries they
// are built on (Next.js before React, Nuxt before Vue...).
const JS_RULES = [
  {
    id: "nextjs",
    label: "Next.js",
    match: (d) => d.next,
    react: true,
    envFile: ".env.local",
    envVar: "NEXT_PUBLIC_LIKYLY_KEY",
    starter: (dir) => {
      const appRouter = has(dir, "app", "src/app");
      return appRouter
        ? { path: `${srcPrefix(dir)}components/likyly-provider.tsx`, template: "next-app" }
        : { path: `${srcPrefix(dir)}components/likyly-provider.tsx`, template: "react", envExpr: "process.env.NEXT_PUBLIC_LIKYLY_KEY" };
    },
  },
  { id: "nuxt", label: "Nuxt", match: (d) => d.nuxt, envFile: ".env", envVar: "NUXT_PUBLIC_LIKYLY_KEY", starter: () => ({ path: "plugins/likyly.client.ts", template: "nuxt" }) },
  { id: "sveltekit", label: "SvelteKit", match: (d) => d["@sveltejs/kit"], envFile: ".env", envVar: "PUBLIC_LIKYLY_KEY", starter: () => ({ path: "src/lib/likyly.ts", template: "sveltekit" }) },
  {
    id: "remix",
    label: "Remix / React Router",
    match: (d) => d["@remix-run/react"] || d["@react-router/dev"],
    react: true,
    envFile: ".env",
    envVar: "VITE_LIKYLY_KEY",
    starter: () => ({ path: "app/components/likyly-provider.tsx", template: "react", envExpr: "import.meta.env.VITE_LIKYLY_KEY" }),
  },
  { id: "astro", label: "Astro", match: (d) => d.astro, envFile: ".env", envVar: "PUBLIC_LIKYLY_KEY", starter: () => ({ path: "src/lib/likyly.ts", template: "vanilla", envExpr: "import.meta.env.PUBLIC_LIKYLY_KEY" }) },
  { id: "angular", label: "Angular", match: (d) => d["@angular/core"], envFile: null, envVar: null, starter: () => null },
  {
    id: "react-vite",
    label: "React (Vite)",
    match: (d) => d.react && d.vite,
    react: true,
    envFile: ".env.local",
    envVar: "VITE_LIKYLY_KEY",
    starter: () => ({ path: "src/likyly-provider.tsx", template: "react", envExpr: "import.meta.env.VITE_LIKYLY_KEY" }),
  },
  {
    id: "react-cra",
    label: "React (Create React App)",
    match: (d) => d.react && d["react-scripts"],
    react: true,
    envFile: ".env.local",
    envVar: "REACT_APP_LIKYLY_KEY",
    starter: () => ({ path: "src/likyly-provider.tsx", template: "react", envExpr: "process.env.REACT_APP_LIKYLY_KEY" }),
  },
  { id: "vue", label: "Vue", match: (d) => d.vue, envFile: ".env.local", envVar: "VITE_LIKYLY_KEY", starter: () => ({ path: "src/likyly.ts", template: "vanilla", envExpr: "import.meta.env.VITE_LIKYLY_KEY" }) },
  { id: "react", label: "React", match: (d) => d.react, react: true, envFile: ".env", envVar: "LIKYLY_KEY", starter: () => null },
  {
    id: "node",
    label: "Node.js (server)",
    match: (d) => d.express || d.fastify || d.koa || d.hono || d["@nestjs/core"],
    envFile: ".env",
    envVar: "LIKYLY_KEY",
    starter: () => null,
  },
];

function detectJs(dir) {
  const pkg = readJson(join(dir, "package.json"));
  if (!pkg) return null;
  const deps = { ...pkg.dependencies, ...pkg.devDependencies, ...pkg.peerDependencies };
  const rule = JS_RULES.find((r) => r.match(deps)) ?? {
    id: "javascript",
    label: "JavaScript / Node.js",
    envFile: ".env",
    envVar: "LIKYLY_KEY",
    starter: () => null,
  };
  const pm = detectJsPackageManager(dir);
  const packages = rule.react ? [JS_SDK, REACT_SDK] : [JS_SDK];
  return {
    id: rule.id,
    label: rule.label,
    language: "typescript",
    sdkDocs: "typescript",
    packages,
    install: { cmd: jsAddCommand(pm, packages), run: true },
    packageManager: pm,
    envFile: rule.envFile,
    envVar: rule.envVar,
    starter: rule.starter(dir),
  };
}

function detectPhp(dir) {
  if (has(dir, "wp-config.php", "wp-config-sample.php", "wp-content")) {
    return {
      id: "wordpress",
      label: "WordPress / WooCommerce",
      language: "php",
      sdkDocs: "php",
      packages: [],
      install: null,
      plugin: true,
      envFile: null,
      envVar: null,
      starter: null,
    };
  }
  const composer = readJson(join(dir, "composer.json"));
  if (!composer) return null;
  const req = { ...composer.require, ...composer["require-dev"] };
  const base = { language: "php", sdkDocs: "php", packages: ["likyly/sdk"], install: { cmd: ["composer", "require", "likyly/sdk"], run: true }, starter: null };
  // Laravel gets its own package (likyly/laravel, on top of likyly/sdk): Blade component, catalog sync
  // from Eloquent, events. `php artisan likyly:install` then wires the product model and the secret key.
  if (req["laravel/framework"]) {
    return {
      ...base,
      id: "laravel",
      label: "Laravel",
      packages: ["likyly/laravel"],
      install: { cmd: ["composer", "require", "likyly/laravel"], run: true },
      next: "php artisan likyly:install",
      envFile: ".env",
      envVar: "LIKYLY_KEY",
    };
  }
  if (req["drupal/core"] || req["drupal/core-recommended"]) return { ...base, id: "drupal", label: "Drupal", envFile: ".env", envVar: "LIKYLY_KEY" };
  if (req["symfony/framework-bundle"]) return { ...base, id: "symfony", label: "Symfony", envFile: ".env.local", envVar: "LIKYLY_KEY" };
  return { ...base, id: "php", label: "PHP", envFile: ".env", envVar: "LIKYLY_KEY" };
}

function detectPython(dir) {
  if (!has(dir, "pyproject.toml", "requirements.txt", "setup.py", "Pipfile", "manage.py")) return null;
  const blob = ["pyproject.toml", "requirements.txt", "Pipfile", "setup.py"].map((f) => readText(join(dir, f))).join("\n").toLowerCase();
  let id = "python";
  let label = "Python";
  if (has(dir, "manage.py") || /\bdjango\b/.test(blob)) [id, label] = ["django", "Django"];
  else if (/\bfastapi\b/.test(blob)) [id, label] = ["fastapi", "FastAPI"];
  else if (/\bflask\b/.test(blob)) [id, label] = ["flask", "Flask"];
  return { id, label, language: "python", sdkDocs: "python", packages: ["likyly"], install: pythonAddCommand(dir), envFile: ".env", envVar: "LIKYLY_KEY", starter: null };
}

function detectOthers(dir) {
  if (has(dir, "Gemfile")) {
    const rails = /\brails\b/.test(readText(join(dir, "Gemfile")));
    return {
      id: rails ? "rails" : "ruby",
      label: rails ? "Ruby on Rails" : "Ruby",
      language: "ruby",
      sdkDocs: "ruby",
      packages: ["likyly"],
      install: { cmd: ["bundle", "add", "likyly"], run: true },
      envFile: ".env",
      envVar: "LIKYLY_KEY",
      starter: null,
    };
  }
  if (has(dir, "go.mod")) {
    return { id: "go", label: "Go", language: "go", sdkDocs: "go", packages: ["github.com/likyly/likyly-go"], install: { cmd: ["go", "get", "github.com/likyly/likyly-go"], run: true }, envFile: ".env", envVar: "LIKYLY_KEY", starter: null };
  }
  if (has(dir, "Cargo.toml")) {
    return { id: "rust", label: "Rust", language: "rust", sdkDocs: "rust", packages: ["likyly"], install: { cmd: ["cargo", "add", "likyly"], run: true }, envFile: ".env", envVar: "LIKYLY_KEY", starter: null };
  }
  let entries = [];
  try {
    entries = readdirSync(dir);
  } catch {
    /* unreadable dir: nothing to detect */
  }
  if (entries.some((f) => f.endsWith(".csproj") || f.endsWith(".sln"))) {
    return { id: "dotnet", label: ".NET", language: "dotnet", sdkDocs: "dotnet", packages: ["Likyly"], install: { cmd: ["dotnet", "add", "package", "Likyly"], run: true }, envFile: null, envVar: null, starter: null };
  }
  if (has(dir, "pom.xml", "build.gradle", "build.gradle.kts")) {
    // Editing a pom.xml/Gradle build safely is not a one-liner: print the coordinates.
    return { id: "java", label: "Java", language: "java", sdkDocs: "java", packages: ["com.likyly:likyly-sdk"], install: { cmd: ["# add the dependency", "com.likyly:likyly-sdk"], run: false }, envFile: null, envVar: null, starter: null };
  }
  return null;
}

/**
 * A Shopify theme (Online Store 2.0), as `shopify theme pull` writes it. No package to install:
 * the section loads the SDK from jsDelivr, the public key lives in a snippet, and the developer
 * adds the section from the theme editor.
 */
function detectShopify(dir) {
  if (!has(dir, "layout/theme.liquid") || !has(dir, "config/settings_schema.json")) return null;
  return {
    id: "shopify",
    label: "Shopify theme",
    language: "liquid",
    sdkDocs: "typescript",
    packages: [],
    install: null,
    envFile: null,
    envVar: null,
    keyFile: "snippets/likyly-key.liquid",
    starter: { path: "sections/likyly-recommendations.liquid", template: "shopify-section" },
    next: "shopify theme dev (preview), then shopify theme push",
  };
}

/**
 * A Shopify app (shopify.app.toml, Shopify CLI's app template). Its storefront goes through a
 * theme app extension and its server holds the secret key: the browser SDK and React provider
 * the JS rules would add have no place here, so nothing is installed or written.
 */
function detectShopifyApp(dir) {
  if (!has(dir, "shopify.app.toml")) return null;
  return {
    id: "shopify-app",
    label: "Shopify app",
    language: "javascript",
    sdkDocs: "typescript",
    packages: [],
    install: null,
    envFile: null,
    envVar: null,
    starter: null,
    notes: [
      "A Shopify app: nothing to install here. Call LIKYLY from the app's server with the secret key (npm install @likyly/sdk), and show recommendations through a theme app extension.",
      "To add LIKYLY to a store's theme instead, run the wizard in the theme (shopify theme pull).",
    ],
  };
}

/** Detect the project in `dir`. Returns `null` when nothing recognisable is found. */
export function detectProject(dir) {
  // A Shopify theme or app first: their tooling adds a package.json, they are still Shopify.
  const shopify = detectShopify(dir) ?? detectShopifyApp(dir);
  if (shopify) return shopify;
  // WordPress next: a WP install often ships a package.json for its theme tooling.
  const wp = has(dir, "wp-config.php", "wp-config-sample.php", "wp-content") ? detectPhp(dir) : null;
  // A PHP framework next to a package.json (Laravel ships Vite, Symfony may ship Encore) is a PHP
  // project: its package.json only builds the front assets.
  const php = detectPhp(dir);
  const phpFramework = php && php.id !== "php" ? php : null;
  return wp ?? phpFramework ?? detectJs(dir) ?? php ?? detectPython(dir) ?? detectOthers(dir);
}

/**
 * AI coding tools whose project-level MCP config we know how to write (formats checked against
 * each tool's docs, 2026-11). Per tool:
 * - `file` / `format`: the MCP config (JSON under `serversKey`, or a TOML block for Codex and
 *   Mistral Vibe);
 * - `keyRef`: how the tool expands an environment variable in that file, when it does so without
 *   extra setup. Tools without one get no key reference at all: the LIKYLY server then reads the
 *   developer key from the environment or from .likyly/credentials.env (found from its working
 *   directory, the project);
 * - `workspaceVar`: the tool's variable for the project folder, when it has one;
 * - `skillDir`: where the tool reads project skills (`.agents/skills` is the shared one);
 * - `detect`: folders that show the project already uses the tool; `commands` / `apps`: signs the
 *   tool is installed on this machine;
 * - `trust`: the tool only reads project config in a folder the developer trusted.
 * Not here: Windsurf / Devin Desktop (MCP config is global only) and opencode (config format in
 * flux).
 */
const JETBRAINS_IDES = ["IntelliJ IDEA", "IntelliJ IDEA Ultimate", "IntelliJ IDEA CE", "PhpStorm", "WebStorm", "PyCharm", "PyCharm Professional Edition", "PyCharm CE", "GoLand", "Rider", "RubyMine", "CLion", "RustRover"];
export const EDITORS = [
  { id: "claude-code", label: "Claude Code", file: ".mcp.json", format: "json", serversKey: "mcpServers", keyRef: "${LIKYLY_DEVELOPER_KEY:-}", skillDir: ".claude/skills", detect: (dir) => has(dir, ".mcp.json", ".claude", "CLAUDE.md"), commands: ["claude"], apps: [] },
  { id: "cursor", label: "Cursor", file: ".cursor/mcp.json", format: "json", serversKey: "mcpServers", keyRef: "${env:LIKYLY_DEVELOPER_KEY}", workspaceVar: "${workspaceFolder}", skillDir: ".agents/skills", detect: (dir) => has(dir, ".cursor", ".cursorrules"), commands: ["cursor"], apps: ["Cursor"] },
  { id: "vscode", label: "VS Code (Copilot)", file: ".vscode/mcp.json", format: "json", serversKey: "servers", keyRef: "${env:LIKYLY_DEVELOPER_KEY}", workspaceVar: "${workspaceFolder}", skillDir: ".github/skills", detect: (dir) => has(dir, ".vscode"), commands: ["code"], apps: ["Visual Studio Code"] },
  { id: "codex", label: "Codex (OpenAI)", file: ".codex/config.toml", format: "codex-toml", skillDir: ".agents/skills", detect: (dir) => has(dir, ".codex"), commands: ["codex"], apps: ["Codex"], trust: true },
  { id: "antigravity", label: "Antigravity (Google, ex-Gemini CLI)", file: ".agents/mcp_config.json", format: "json", serversKey: "mcpServers", skillDir: ".agents/skills", detect: (dir) => has(dir, ".agents/mcp_config.json", ".gemini"), commands: ["antigravity", "agy", "gemini"], apps: ["Antigravity"] },
  { id: "mistral-vibe", label: "Mistral Vibe", file: ".vibe/config.toml", format: "vibe-toml", skillDir: ".agents/skills", detect: (dir) => has(dir, ".vibe"), commands: ["vibe"], apps: [], trust: true },
  { id: "kiro", label: "Kiro (AWS)", file: ".kiro/settings/mcp.json", format: "json", serversKey: "mcpServers", skillDir: ".kiro/skills", detect: (dir) => has(dir, ".kiro"), commands: ["kiro", "kiro-cli"], apps: ["Kiro"] },
  { id: "junie", label: "Junie (JetBrains)", file: ".junie/mcp/mcp.json", format: "json", serversKey: "mcpServers", skillDir: ".junie/skills", detect: (dir) => has(dir, ".junie"), commands: ["junie"], apps: JETBRAINS_IDES },
];

export const editorById = (id) => EDITORS.find((e) => e.id === id);

function onPath(bin) {
  const probe = process.platform === "win32" ? ["where", [bin]] : ["sh", ["-c", `command -v ${JSON.stringify(bin)}`]];
  return spawnSync(probe[0], probe[1], { stdio: "ignore" }).status === 0;
}

/** The tools installed on this machine: a command on the PATH, or (macOS) the app. */
export function installedEditors({ home = homedir(), platform = process.platform, commandExists = onPath } = {}) {
  const appInstalled = (app) =>
    platform === "darwin" && [join("/Applications", `${app}.app`), join(home, "Applications", `${app}.app`)].some((p) => existsSync(p));
  return EDITORS.filter((e) => e.commands.some(commandExists) || e.apps.some(appInstalled)).map((e) => e.id);
}

export function detectEditors(dir) {
  return EDITORS.filter((e) => e.detect(dir)).map((e) => e.id);
}
