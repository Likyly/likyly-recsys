// The file-writing and command-running steps. Every step is non-destructive: it appends or
// creates, never overwrites something the developer already has.

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

/** Keys that must never reach a browser: secret (`sk_`) and developer (`lk_`) keys. */
export function isServerOnlyKey(key) {
  return /^(sk_|lk_)/.test(key.trim());
}

/** Add `VAR=value` to the env file unless the variable is already set there. */
export function writeEnv(dir, envFile, envVar, value, { dryRun = false } = {}) {
  const path = join(dir, envFile);
  const current = existsSync(path) ? readFileSync(path, "utf8") : "";
  if (new RegExp(`^\\s*${envVar}\\s*=`, "m").test(current)) return { path, status: "exists" };
  if (!dryRun) {
    const sep = current && !current.endsWith("\n") ? "\n" : "";
    appendFileSync(path, `${sep}# LIKYLY - clé publique (sans danger dans le navigateur)\n${envVar}=${value}\n`);
  }
  return { path, status: "added" };
}

const TEMPLATES = {
  "next-app": () => `"use client";

// Généré par @likyly/wizard. Enveloppez votre application une seule fois, dans app/layout.tsx :
//   <LikylyRoot>{children}</LikylyRoot>
// puis affichez des recommandations n'importe où avec <LikylyRecommendations placement="..." />.
import type { ReactNode } from "react";
import { LikylyProvider } from "@likyly/react";

export default function LikylyRoot({ children }: { children: ReactNode }) {
  return <LikylyProvider apiKey={process.env.NEXT_PUBLIC_LIKYLY_KEY!}>{children}</LikylyProvider>;
}
`,
  react: (expr) => `// Généré par @likyly/wizard. Enveloppez votre application une seule fois :
//   <LikylyRoot><App /></LikylyRoot>
// puis affichez des recommandations n'importe où avec <LikylyRecommendations placement="..." />.
import type { ReactNode } from "react";
import { LikylyProvider } from "@likyly/react";

export default function LikylyRoot({ children }: { children: ReactNode }) {
  return <LikylyProvider apiKey={${expr}}>{children}</LikylyProvider>;
}
`,
  nuxt: () => `// Généré par @likyly/wizard. Déclarez la clé dans nuxt.config.ts :
//   runtimeConfig: { public: { likylyKey: "" } }   // lue depuis NUXT_PUBLIC_LIKYLY_KEY
// puis utilisez useNuxtApp().$likyly dans vos composants.
import { Likyly } from "@likyly/sdk";

export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig();
  return { provide: { likyly: new Likyly({ apiKey: String(config.public.likylyKey) }) } };
});
`,
  sveltekit: () => `// Généré par @likyly/wizard. Importez \`likyly\` depuis "$lib/likyly" dans vos composants.
import { env } from "$env/dynamic/public";
import { Likyly } from "@likyly/sdk";

export const likyly = new Likyly({ apiKey: env.PUBLIC_LIKYLY_KEY ?? "" });
`,
  vanilla: (expr) => `// Généré par @likyly/wizard. Importez \`likyly\` là où vous affichez des recommandations.
import { Likyly } from "@likyly/sdk";

export const likyly = new Likyly({ apiKey: ${expr} });
`,
};

/** Write the framework's starter file, unless a file already exists at that path. */
export function writeStarter(dir, starter, { dryRun = false } = {}) {
  if (!starter) return null;
  const path = join(dir, starter.path);
  if (existsSync(path)) return { path, status: "exists" };
  const content = TEMPLATES[starter.template](starter.envExpr);
  if (!dryRun) {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content);
  }
  return { path, status: "created" };
}

/** The LIKYLY skill, bundled at publish time; falls back to the monorepo copy in development. */
export function readSkill() {
  for (const p of [join(HERE, "..", "skill", "SKILL.md"), join(HERE, "..", "..", "..", "docs", "SKILL.md")]) {
    if (existsSync(p)) return readFileSync(p, "utf8");
  }
  return null;
}

/** Install the skill for Claude Code (`.claude/skills/likyly/SKILL.md`). */
export function writeSkill(dir, { dryRun = false } = {}) {
  const skill = readSkill();
  if (!skill) return null;
  const path = join(dir, ".claude", "skills", "likyly", "SKILL.md");
  const status = existsSync(path) ? "updated" : "created";
  if (!dryRun) {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, skill);
  }
  return { path, status };
}

export function commandExists(bin) {
  const probe = process.platform === "win32" ? ["where", [bin]] : ["sh", ["-c", `command -v ${JSON.stringify(bin)}`]];
  return spawnSync(probe[0], probe[1], { stdio: "ignore" }).status === 0;
}

/** Run a command in `dir`, streaming its output. Returns true on success. */
export function run(dir, cmd) {
  const res = spawnSync(cmd[0], cmd.slice(1), { cwd: dir, stdio: "inherit", shell: process.platform === "win32" });
  return res.status === 0;
}
