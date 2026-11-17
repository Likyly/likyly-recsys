// Git, so the developer can review what LIKYLY changed before keeping it: the wizard works on a
// branch of its own (likyly/setup) and lists, at the end, the files it changed - apart from the
// ones that were already modified before it started. It never commits.

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

export const BRANCH = "likyly/setup";

function git(dir, args) {
  const res = spawnSync("git", args, { cwd: dir, encoding: "utf8" });
  return res.status === 0 ? res.stdout : null;
}

export function isRepo(dir) {
  return git(dir, ["rev-parse", "--is-inside-work-tree"])?.trim() === "true";
}

export function currentBranch(dir) {
  return git(dir, ["branch", "--show-current"])?.trim() || null;
}

/** The repository's folder name (e.g. "my-shop"), to say which repository the branch goes in. */
export function repoName(dir) {
  const top = git(dir, ["rev-parse", "--show-toplevel"])?.trim();
  return top ? top.split(/[\\/]/).pop() : null;
}

/** The branch uninstall works on, when the developer picks a branch of its own. */
export const UNINSTALL_BRANCH = "likyly/uninstall";

/** Whether a branch already exists (e.g. the wizard ran here before). */
export function branchExists(dir, name = BRANCH) {
  return git(dir, ["rev-parse", "--verify", "--quiet", `refs/heads/${name}`]) !== null;
}

/** `{ path: "M" | "A" | "D" | "?" }` for every changed or untracked (not ignored) file. */
export function changedFiles(dir) {
  const out = git(dir, ["status", "--porcelain", "--untracked-files=all"]);
  if (out === null) return {};
  const files = {};
  for (const line of out.split("\n")) {
    if (!line.trim()) continue;
    const code = line.slice(0, 2);
    const path = line.slice(3).replace(/^"|"$/g, "").split(" -> ").pop();
    files[path] = code.includes("?") ? "A" : code.includes("D") ? "D" : code.includes("A") ? "A" : "M";
  }
  return files;
}

/** Which of `paths` git ignores (they will not show up in a review: say so). */
export function ignored(dir, paths) {
  if (!paths.length) return [];
  const res = spawnSync("git", ["check-ignore", ...paths], { cwd: dir, encoding: "utf8" });
  return res.stdout ? res.stdout.split("\n").filter(Boolean) : [];
}

/** Start a branch from where the developer is. Uncommitted work comes along, untouched. */
export function createBranch(dir, name = BRANCH) {
  const exists = branchExists(dir, name);
  const ok = git(dir, exists ? ["switch", name] : ["switch", "-c", name]) !== null;
  return { ok, existed: exists };
}

function fingerprint(dir, path) {
  try {
    return createHash("sha1").update(readFileSync(join(dir, path))).digest("hex");
  } catch {
    return "missing";
  }
}

/** What the project looks like before the wizard touches it: changed files and their content. */
export function snapshot(dir) {
  const files = changedFiles(dir);
  return Object.fromEntries(Object.keys(files).map((path) => [path, fingerprint(dir, path)]));
}

/**
 * The files the wizard changed: changed now, and either not changed at the snapshot or with a
 * different content since. `alreadyModified` counts the developer's own pending changes.
 */
export function changesSince(dir, before) {
  const now = changedFiles(dir);
  const mine = Object.entries(now)
    .filter(([path]) => before[path] === undefined || before[path] !== fingerprint(dir, path))
    .map(([path, code]) => ({ path, code }));
  return { mine, alreadyModified: Object.keys(before).length };
}

// Lock files: thousands of lines nobody reviews by eye; named, not shown.
const TOOLING = /(^|\/)skills\/likyly\/|^\.claude\/commands\/likyly-/;
const LOCKFILES = /(^|\/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|composer\.lock|poetry\.lock|uv\.lock|Gemfile\.lock|Cargo\.lock|go\.sum)$/;

/**
 * The changes `files` ([{ path, code }]) as coloured diff lines, to show inside the wizard: the
 * modified files' diff, each new file's content, lock files only named. Long output is cut
 * (`max` lines) with a pointer to the full view.
 */
export function diffLines(dir, files, { max = 150, color = true } = {}) {
  const lines = [];
  const opts = color ? ["--color=always"] : ["--no-color"];
  const run = (args) => {
    const r = spawnSync("git", ["--no-pager", "-C", dir, ...args], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
    return r.stdout ?? "";
  };
  const locks = files.filter((f) => LOCKFILES.test(f.path));
  // The assistant's own tooling (the LIKYLY skill, Claude Code's commands): long, the same in every
  // project, nothing of the site in them. Counted, not shown.
  const tooling = files.filter((f) => TOOLING.test(f.path));
  const shown = files.filter((f) => !LOCKFILES.test(f.path) && !TOOLING.test(f.path));
  const tracked = shown.filter((f) => f.code !== "A").map((f) => f.path);
  const added = shown.filter((f) => f.code === "A").map((f) => f.path);
  if (tracked.length) lines.push(...run(["diff", ...opts, "--", ...tracked]).split("\n"));
  // A new file is not in git yet: compared with nothing, it shows whole, as added lines.
  for (const path of added) lines.push(...run(["diff", "--no-index", ...opts, "--", "/dev/null", path]).split("\n"));
  for (const f of locks) lines.push(`${f.path}: lock file updated (not shown)`);
  if (tooling.length) lines.push(`+ ${tooling.length} file(s) for your assistant, not shown: the LIKYLY skill${tooling.some((f) => f.path.startsWith(".claude/commands/")) ? " and Claude Code's /likyly commands" : ""}`);
  const out = lines.filter((l, i) => l !== "" || i < lines.length - 1);
  if (out.length <= max) return out;
  return [...out.slice(0, max), `… ${out.length - max} more lines: git diff, or VS Code Source Control (Ctrl/Cmd+Shift+G)`];
}
