// Is this project's stack supported by the LIKYLY SDK? Reads the versions actually installed
// (node_modules), else the range declared in package.json.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

// Minimum major version the SDK supports, per package (React: the @likyly/react peer range).
const SUPPORT = {
  next: { label: "Next.js", min: 13 },
  react: { label: "React", min: 18, max: 19 },
  nuxt: { label: "Nuxt", min: 3 },
  vue: { label: "Vue", min: 3 },
  "@sveltejs/kit": { label: "SvelteKit", min: 1 },
  astro: { label: "Astro", min: 3 },
  "@angular/core": { label: "Angular", min: 15 },
  "@remix-run/react": { label: "Remix", min: 2 },
  "@react-router/dev": { label: "React Router", min: 7 },
};
const NODE_MIN = 18;

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

/** Installed version, else the declared range with its operators stripped ("^16.0.1" -> "16.0.1"). */
export function packageVersion(dir, name) {
  const installed = readJson(join(dir, "node_modules", name, "package.json"))?.version;
  if (installed) return { version: installed, installed: true };
  const pkg = readJson(join(dir, "package.json"));
  const range = pkg && { ...pkg.dependencies, ...pkg.devDependencies }[name];
  const m = typeof range === "string" && range.match(/\d+(\.\d+){0,2}/);
  return m ? { version: m[0], installed: false } : null;
}

const major = (v) => Number(String(v).split(".")[0]);

/** `[{ label, version, ok, need }]` for the stack in `dir`, and an overall `ok`. */
export function checkCompat(dir, project) {
  const items = [];
  if (project?.language === "typescript" && existsSync(join(dir, "package.json"))) {
    for (const [name, rule] of Object.entries(SUPPORT)) {
      const v = packageVersion(dir, name);
      if (!v) continue;
      const m = major(v.version);
      const ok = m >= rule.min && (rule.max === undefined || m <= rule.max);
      items.push({ label: rule.label, version: v.version, ok, need: rule.max ? `${rule.min}–${rule.max}` : `${rule.min}+` });
    }
  }
  const node = process.versions.node;
  items.push({ label: "Node.js", version: node, ok: major(node) >= NODE_MIN, need: `${NODE_MIN}+` });
  return { items, ok: items.every((i) => i.ok) };
}

export const describeCompat = (items) => items.map((i) => `${i.label} ${i.version}`).join(" · ");
