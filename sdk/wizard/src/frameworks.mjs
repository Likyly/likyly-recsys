// Framework detection: look at the project's own manifest files (never guess from the
// folder name) and return everything the later steps need - which SDK packages to add, how to
// add them, which env file and variable name the framework exposes to the browser, and which
// starter file (if any) to write.

import { existsSync, readFileSync, readdirSync } from "node:fs";
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
  if (req["laravel/framework"]) return { ...base, id: "laravel", label: "Laravel", envFile: ".env", envVar: "LIKYLY_KEY" };
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

/** Detect the project in `dir`. Returns `null` when nothing recognisable is found. */
export function detectProject(dir) {
  // WordPress first: a WP install often ships a package.json for its theme tooling.
  const wp = has(dir, "wp-config.php", "wp-config-sample.php", "wp-content") ? detectPhp(dir) : null;
  return wp ?? detectJs(dir) ?? detectPhp(dir) ?? detectPython(dir) ?? detectOthers(dir);
}

/** AI coding tools whose project-level MCP config we know how to write. */
export const EDITORS = [
  { id: "claude-code", label: "Claude Code", file: ".mcp.json", detect: (dir) => has(dir, ".mcp.json", ".claude", "CLAUDE.md") },
  { id: "cursor", label: "Cursor", file: ".cursor/mcp.json", detect: (dir) => has(dir, ".cursor", ".cursorrules") },
  { id: "vscode", label: "VS Code (Copilot)", file: ".vscode/mcp.json", detect: (dir) => has(dir, ".vscode") },
];

export function detectEditors(dir) {
  return EDITORS.filter((e) => e.detect(dir)).map((e) => e.id);
}
