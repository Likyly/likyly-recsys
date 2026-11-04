// The interactive wizard: a vertical timeline of steps, one live at a time. Finished steps are
// printed once (Ink <Static>) so the live frame stays short. It ends verified, on a single
// choice: launch the AI assistant with the integration prompt.

import { spawn, spawnSync } from "node:child_process";
import { homedir } from "node:os";
import { useEffect, useRef, useState } from "react";
import { Box, Static, Text, render, useApp, useStdout, useWindowSize } from "ink";
import { EDITORS, detectEditors, detectProject } from "../frameworks.mjs";
import { KEY_ENV } from "../mcp.mjs";
import { commandExists } from "../setup.mjs";
import { checkCompat, describeCompat } from "../compat.mjs";
import {
  applyDeveloperKey,
  applyKey,
  applyStarter,
  connectEditors,
  describePlan,
  editorLabel,
  installMode,
  installSdk,
  verifyInstall,
} from "../actions.mjs";
import { AGENT_PROMPT, DOCS_URL, KEYS_URL, TAGLINE } from "../constants.mjs";
import { C, Gradient, Logo, Tag } from "./theme.jsx";
import { LiveLog, MultiSelect, Results, Select, Step } from "./widgets.jsx";
import { AccountStep } from "./account.jsx";
import { copyToClipboard } from "./system.mjs";
import * as git from "../git.mjs";

const INSTALL_STEPS = [
  ["detect", "Detect your project"],
  ["plan", "Review the plan"],
  ["sdk", "Install the SDK"],
  ["key", "Connect your LIKYLY account"],
  ["mcp", "Connect your AI assistant"],
  ["verify", "Verify the installation"],
];
const MCP_STEPS = [
  ["mcp", "Connect your AI assistant"],
  ["verify", "Verify the installation"],
];

// Below this, Ink can no longer redraw the live step in place (it would repeat on screen).
const MIN_COLUMNS = 64;
const MIN_ROWS = 20;

const statusOf = (results) =>
  results.some((r) => r.level === "fail") ? "fail" : results.some((r) => r.level === "warn") ? "warn" : "done";
const maskKey = (k) => (k.length > 14 ? `${k.slice(0, 8)}…${k.slice(-4)}` : k);

/** Shown instead of the live step while the terminal is too small; the wizard resumes on resize. */
function TooSmall({ columns, rows }) {
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={C.amber} paddingX={1} marginX={1}>
      <Text color={C.amber} bold>
        Make this window bigger
      </Text>
      <Text>
        The wizard needs at least {MIN_COLUMNS}×{MIN_ROWS}, this one is {columns}×{rows}.
      </Text>
      <Text color={C.muted}>It picks up where it was as soon as there is room.</Text>
    </Box>
  );
}

function Header({ opts, version }) {
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Logo version={version} />
      <Box marginTop={1}>
        <Gradient bold>{TAGLINE}</Gradient>
      </Box>
      <Box gap={2}>
        <Box flexShrink={1}>
          <Text color={C.muted} wrap="truncate-start">
            {opts.dir.startsWith(homedir()) ? `~${opts.dir.slice(homedir().length)}` : opts.dir}
          </Text>
        </Box>
        {opts.dryRun ? <Tag color={C.amber}>DRY RUN</Tag> : null}
        {opts.hosted ? <Tag color={C.violet}>HOSTED MCP</Tag> : null}
      </Box>
    </Box>
  );
}

function PlanCard({ project, rows, compat }) {
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={C.violet} paddingX={1} width={74}>
      <Text>
        <Text color={C.violet} bold>
          {project.label}
        </Text>
        {project.packageManager ? <Text color={C.muted}> · {project.packageManager}</Text> : null}
      </Text>
      <Box>
        <Box width={9} flexShrink={0}>
          <Text color={C.muted}>Check</Text>
        </Box>
        <Text>
          {describeCompat(compat.items)}{" "}
          {compat.ok ? <Text color={C.mint}>✓ supported</Text> : <Text color={C.coral}>✗ {compat.items.filter((i) => !i.ok).map((i) => `${i.label} needs ${i.need}`).join(", ")}</Text>}
        </Text>
      </Box>
      {rows.map(([verb, what]) => (
        <Box key={verb + what}>
          <Box width={9} flexShrink={0}>
            <Text color={C.muted}>{verb}</Text>
          </Box>
          <Text>{what}</Text>
        </Box>
      ))}
      <Text color={C.muted}>Nothing existing is overwritten.</Text>
    </Box>
  );
}

/** What LIKYLY changed in the project, for a review before keeping it. Printed once. */
function ReviewPanel({ review }) {
  const { mine, alreadyModified, branch, notInGit } = review;
  const shown = mine.slice(0, 12);
  const mark = { M: ["M", C.amber], A: ["+", C.mint], D: ["-", C.coral] };
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text bold>
        Changed in your project{branch ? <Text color={C.muted}> · branch {branch}</Text> : null}
        <Text color={C.muted}> · nothing committed</Text>
      </Text>
      {mine.length === 0 ? <Text color={C.muted}>  No file changed.</Text> : null}
      {shown.map((f) => (
        <Text key={f.path}>
          {"  "}
          <Text color={(mark[f.code] ?? mark.M)[1]} bold>
            {(mark[f.code] ?? mark.M)[0]}
          </Text>{" "}
          {f.path}
        </Text>
      ))}
      {mine.length > shown.length ? <Text color={C.muted}>  … and {mine.length - shown.length} more</Text> : null}
      {notInGit.length ? <Text color={C.muted}>  Not in git (ignored on purpose): {notInGit.join(", ")}</Text> : null}
      {alreadyModified ? (
        <Text color={C.muted}>
          {"  "}
          {alreadyModified === 1 ? "Your 1 pending change from before is" : `Your ${alreadyModified} pending changes from before are`} left as they were.
        </Text>
      ) : null}
      <Text color={C.muted}>
        {"  "}Review: VS Code Source Control (Ctrl/Cmd+Shift+G), or <Text color={C.sky}>git diff</Text>
        {branch && branch.startsWith("likyly/") ? " · keep it: merge the branch; drop it: switch back and delete it" : ""}
      </Text>
    </Box>
  );
}

/** The closing panel, printed once: what is done, and what the assistant will do next. */
function DonePanel({ editors, dryRun, level, devKey }) {
  const failed = level === "fail";
  const { stdout } = useStdout();
  const width = Math.min((stdout?.columns ?? 80) - 2, 86);
  const needsDevKey = editors.length && !devKey && !dryRun;
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={failed ? C.coral : level === "warn" ? C.amber : C.mint} paddingX={2} paddingY={1} width={width}>
      <Text>
        <Text color={failed ? C.coral : level === "warn" ? C.amber : C.mint} bold>
          {failed ? "✗ " : level === "warn" ? "! " : "✓ "}
        </Text>
        <Gradient bold>
          {failed
            ? "Almost there: fix the red lines above, then run the wizard again."
            : dryRun
              ? "Dry run done. Nothing was written."
              : level === "warn"
                ? "LIKYLY is set up. Finish the ! lines above."
                : "LIKYLY is installed and verified."}
        </Gradient>
      </Text>
      {!failed ? (
        <Box flexDirection="column" marginTop={1}>
          <Text>
            What&apos;s left needs your code: your assistant connects your catalog, creates a first placement and wires it into your pages.
          </Text>
          {needsDevKey ? (
            <Box flexDirection="column" marginTop={1}>
              <Text color={C.amber}>Your assistant needs a developer key first:</Text>
              <Text>
                <Text color={C.sky}>{KEYS_URL}</Text>
                <Text color={C.muted}> then </Text>
                <Text color={C.mint}>export {KEY_ENV}=lk_…</Text>
              </Text>
            </Box>
          ) : null}
          <Box marginTop={1} borderStyle="bold" borderLeft borderTop={false} borderRight={false} borderBottom={false} borderColor={C.blue} paddingLeft={1}>
            <Text italic>{AGENT_PROMPT}</Text>
          </Box>
          <Box marginTop={1}>
            <Text color={C.muted}>
              Docs <Text color={C.sky}>{DOCS_URL}</Text>
            </Text>
          </Box>
        </Box>
      ) : null}
    </Box>
  );
}

/** The one remaining choice, Enter by default: hand over to the assistant. */
function NextAction({ editors, canReview, onPick }) {
  const items = [];
  if (editors.includes("claude-code") && commandExists("claude")) items.push({ label: "Launch Claude Code now", value: "claude", hint: "it starts on this prompt" });
  if (canReview) items.push({ label: "Review the changes first", value: "review", hint: "git diff of what LIKYLY changed" });
  if (editors.includes("cursor") && commandExists("cursor")) items.push({ label: "Open Cursor", value: "cursor", hint: "the prompt is copied, paste it in the chat" });
  items.push({ label: "Copy the prompt and exit", value: "copy" });
  items.push({ label: "Exit", value: "exit" });
  return <Select items={items} onSubmit={onPick} />;
}

function App({ opts: initialOpts, result }) {
  const { exit } = useApp();
  const opts = useRef({ ...initialOpts }).current;
  const order = opts.command === "mcp" ? MCP_STEPS : INSTALL_STEPS;
  const [steps, setSteps] = useState(() => Object.fromEntries(order.map(([id]) => [id, { status: "pending", results: [] }])));
  const [phase, setPhase] = useState(order[0][0]);
  const [project] = useState(() => detectProject(opts.dir));
  const [compat] = useState(() => checkCompat(opts.dir, project));
  // Git: where the developer was, and what was already modified, so the end of the run can show
  // exactly what LIKYLY changed (and work on its own branch if they want).
  const [repo] = useState(() =>
    git.isRepo(opts.dir)
      ? { branch: git.currentBranch(opts.dir), name: git.repoName(opts.dir), branchExists: git.branchExists(opts.dir), before: git.snapshot(opts.dir) }
      : null,
  );
  const workBranch = useRef(repo?.branch ?? null);
  const [log, setLog] = useState([]);
  const keys = useRef({ publicKey: opts.publicKey || null, devKey: null });
  const reviewRef = useRef([]);
  const [chosenEditors, setChosenEditors] = useState([]);
  // Everything finished is printed once and never redrawn: only the live step is.
  const [printed, setPrinted] = useState([{ key: "header", kind: "header" }]);
  const { columns, rows } = useWindowSize();
  const tooSmall = columns < MIN_COLUMNS || rows < MIN_ROWS;

  const finish = (id, results, summary, status) => {
    const st = { status: status ?? statusOf(results), results, summary };
    setSteps((s) => ({ ...s, [id]: st }));
    setPrinted((p) => [...p, { key: id, kind: "step", title: order.find(([k]) => k === id)[1], ...st }]);
  };
  const failed = Object.values(steps).some((s) => s.status === "fail");
  const end = (code, launch = null, files = []) => {
    result.code = code;
    result.launch = launch;
    result.files = files;
    exit();
  };

  useEffect(() => {
    if (phase === "detect") {
      if (!project) {
        finish("detect", [{ level: "fail", text: "No framework recognised here. Run it at your project root, or follow", hint: DOCS_URL }]);
        end(1);
        return;
      }
      const stack = describeCompat(compat.items.filter((i) => i.label !== "Node.js"));
      finish("detect", [], [stack || project.label, project.packageManager].filter(Boolean).join(" · "), "done");
      setPhase("plan");
    }
    if (phase === "sdk") {
      let cancelled = false;
      const live = installMode(opts, project) === "run" && !opts.dryRun;
      installSdk(opts, project, live ? (line) => !cancelled && setLog((l) => [...l.slice(-40), line]) : undefined).then((r) => {
        if (cancelled) return;
        finish("sdk", r, project.packages.join(" + ") || undefined);
        setPhase("key");
      });
      return () => {
        cancelled = true;
      };
    }
    if (phase === "key" && (!project.envVar || opts.publicKey)) {
      finish("key", [...applyKey(opts, project, opts.publicKey), ...applyStarter(opts, project)], opts.publicKey ? maskKey(opts.publicKey) : undefined);
      setPhase("mcp");
    }
    if (phase === "mcp" && opts.editors) connect(opts.editors);
    if (phase === "verify") {
      verifyInstall(opts, project, { ...keys.current, editors: chosenEditors }).then((r) => {
        const level = failed || r.some((x) => x.level === "fail") ? "fail" : r.some((x) => x.level === "warn") ? "warn" : "ok";
        finish("verify", r, { fail: "needs attention", warn: "one thing left to do", ok: "all good" }[level]);
        const review = repo && !opts.dryRun ? git.changesSince(opts.dir, repo.before) : null;
        const notInGit = repo ? git.ignored(opts.dir, [project?.envFile, ".likyly/credentials.env"].filter(Boolean)) : [];
        setPrinted((p) => [...p, { key: "done", kind: "done", level, review: review && { ...review, branch: workBranch.current, notInGit } }]);
        reviewRef.current = review?.mine ?? [];
        setPhase("next");
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- each phase runs once
  }, [phase]);

  function connect(editors) {
    setChosenEditors(editors);
    const results = editors.length
      ? connectEditors(opts, editors)
      : [{ level: "warn", text: "No assistant connected. Later:", hint: "npx @likyly/wizard mcp add" }];
    finish("mcp", results, editors.map(editorLabel).join(", ") || undefined);
    setPhase("verify");
  }

  const detected = detectEditors(opts.dir);
  // In a git repository, offer a branch of its own so the developer can review before merging.
  const offerBranch = Boolean(repo && !opts.dryRun && repo.branch !== git.BRANCH);
  const current = repo?.branch ?? "the current branch";
  const body = {
    plan: () => (
      <>
        <PlanCard project={project} rows={describePlan(opts, project)} compat={compat} />
        <Box height={1} />
        {offerBranch ? (
          <Box flexDirection="column" marginBottom={1}>
            <Text>
              Where should LIKYLY make its changes? <Text color={C.muted}>(it never commits)</Text>
            </Text>
            <Text color={C.muted}>
              Your project {repo.name ? <Text bold>{repo.name}</Text> : "here"} is a git repository, on branch <Text bold>{current}</Text>.
            </Text>
          </Box>
        ) : repo && !opts.dryRun ? (
          <Box marginBottom={1}>
            <Text color={C.muted}>
              Your project {repo.name ? <Text bold>{repo.name}</Text> : "here"} is on branch <Text bold>{current}</Text>, made for this setup: the changes go there (never committed).
            </Text>
          </Box>
        ) : null}
        <Select
          items={[
            ...(offerBranch
              ? [
                  {
                    label: repo.branchExists ? `On the branch ${git.BRANCH} (recommended)` : `On a new branch ${git.BRANCH} (recommended)`,
                    value: "branch",
                    hint: repo.branchExists ? `already there; ${current} stays as is` : `from ${current}, which stays as is`,
                  },
                  { label: `Directly on ${current}`, value: "go", hint: "the branch you are on" },
                ]
              : [{ label: repo && !opts.dryRun ? `Set it up on ${current}` : "Set it up", value: "go", hint: opts.dryRun ? "(dry run)" : undefined }]),
            ...(installMode(opts, project) === "run"
              ? [{ label: offerBranch ? "Same as the first, but skip the package install" : "Set it up, but skip the package install", value: "noinstall" }]
              : []),
            { label: "Cancel", value: "cancel", hint: "change nothing" },
          ]}
          onSubmit={(v) => {
            if (v === "cancel") {
              finish("plan", [], "cancelled", "skipped");
              end(0);
              return;
            }
            if (v === "noinstall") opts.install = false;
            const results = [];
            const onBranch = v === "branch" || (v === "noinstall" && offerBranch);
            if (onBranch) {
              const made = git.createBranch(opts.dir);
              if (made.ok) {
                workBranch.current = git.BRANCH;
                results.push({
                  level: "ok",
                  text: `${made.existed ? "Back on the branch" : "On a new branch"} ${git.BRANCH}${repo.name ? ` in ${repo.name}` : ""}`,
                  hint: made.existed ? "(your pending changes came along, untouched)" : `(created from ${repo.branch ?? "HEAD"}; your pending changes came along, untouched)`,
                });
              } else results.push({ level: "warn", text: `Could not create ${git.BRANCH}: staying on ${repo.branch}` });
            }
            finish("plan", results, v === "noinstall" ? "approved, no package install" : `approved${workBranch.current ? ` · branch ${workBranch.current}` : ""}`, "done");
            setPhase("sdk");
          }}
        />
      </>
    ),
    sdk: () => (installMode(opts, project) === "run" && !opts.dryRun ? <LiveLog command={project.install.cmd.join(" ")} lines={log} /> : null),
    key: () =>
      project.envVar && !opts.publicKey ? (
        <AccountStep
          project={project}
          dir={opts.dir}
          onDone={(key, { workspace, devKey, account } = {}) => {
            keys.current = { publicKey: key || null, devKey: devKey || null };
            const summary = key ? [maskKey(key), account ?? workspace].filter(Boolean).join(" · ") : "skipped";
            // Say plainly that the keys arrived: the browser round trip can be quick to miss.
            const received = workspace
              ? [{ level: "ok", text: `Received from likyly.com: public key ${maskKey(key)}${devKey ? " + developer key" : ""}`, hint: `(${[account, workspace].filter(Boolean).join(" · ")})` }]
              : [];
            finish("key", [...received, ...applyKey(opts, project, key), ...applyStarter(opts, project), ...applyDeveloperKey(opts, devKey, { account, workspace })], summary);
            setPhase("mcp");
          }}
        />
      ) : null,
    mcp: () =>
      opts.editors ? null : (
        <>
          <Text color={C.muted}>Which assistants should talk to LIKYLY? No key is ever written in these files.</Text>
          <Box marginTop={1}>
            <MultiSelect
              items={EDITORS.map((e) => ({ value: e.id, label: e.label, hint: e.file, badge: detected.includes(e.id) ? "detected" : undefined }))}
              initial={detected.length ? detected : ["claude-code"]}
              onSubmit={connect}
            />
          </Box>
        </>
      ),
  };

  const index = order.findIndex(([id]) => id === phase);
  return (
    <>
      <Static items={printed}>
        {(item) => (
          <Box key={item.key} flexDirection="column" paddingX={1} paddingTop={item.kind === "header" ? 1 : 0}>
            {item.kind === "header" ? <Header opts={opts} version={result.version} /> : null}
            {item.kind === "step" ? (
              <Step status={item.status} title={item.title} summary={item.summary}>
                {item.results.length ? <Results items={item.results} /> : null}
              </Step>
            ) : null}
            {item.kind === "done" ? (
              <>
                {item.review ? <ReviewPanel review={item.review} /> : null}
                <DonePanel editors={chosenEditors} dryRun={opts.dryRun} level={item.level} devKey={keys.current.devKey} />
              </>
            ) : null}
          </Box>
        )}
      </Static>
      {tooSmall ? <TooSmall columns={columns} rows={rows} /> : null}
      {index >= 0 ? (
        <Box flexDirection="column" paddingX={1} display={tooSmall ? "none" : "flex"}>
          <Step status="active" title={order[index][1]} summary={`${index + 1}/${order.length}`} waiting={phase !== "sdk" && phase !== "verify"} last>
            {body[phase] ? body[phase]() : null}
          </Step>
        </Box>
      ) : null}
      {phase === "next" ? (
        <Box paddingX={1} marginTop={1} display={tooSmall ? "none" : "flex"}>
          <NextAction editors={chosenEditors} canReview={reviewRef.current.length > 0} onPick={(v) => end(failed ? 1 : 0, v, reviewRef.current)} />
        </Box>
      ) : null}
    </>
  );
}

/** After the UI is gone: run what the developer picked, with the terminal handed over. */
function launch(choice, dir, files = []) {
  if (choice === "review") {
    // Modified files as a diff (in the developer's pager), new ones listed: git diff skips them.
    const tracked = files.filter((f) => f.code !== "A").map((f) => f.path);
    const added = files.filter((f) => f.code === "A").map((f) => f.path);
    if (tracked.length) spawnSync("git", ["diff", "--", ...tracked], { cwd: dir, stdio: "inherit" });
    if (added.length) console.log(`\n  New files (open them to read): \n${added.map((f) => `    + ${f}`).join("\n")}`);
    console.log("\n  In VS Code: Source Control (Ctrl/Cmd+Shift+G) shows each change side by side.\n  Then: npx @likyly/wizard again is not needed - run `claude` to let your assistant continue.\n");
    return;
  }
  if (choice === "claude") {
    console.log("\n  Starting Claude Code…\n");
    spawnSync("claude", [AGENT_PROMPT], { cwd: dir, stdio: "inherit", shell: process.platform === "win32" });
  } else if (choice === "cursor") {
    copyToClipboard(AGENT_PROMPT);
    spawn("cursor", [dir], { stdio: "ignore", detached: true }).unref();
    console.log("\n  Cursor is opening. The prompt is in your clipboard: paste it in the chat (Cmd/Ctrl+L).\n");
  } else if (choice === "copy") {
    console.log(copyToClipboard(AGENT_PROMPT) ? "\n  Prompt copied. Paste it into your assistant.\n" : `\n  ${AGENT_PROMPT}\n`);
  }
}

export async function runInteractive(opts, version) {
  const result = { code: 130, version, launch: null };
  const app = render(<App opts={opts} result={result} />, { exitOnCtrlC: true });
  await app.waitUntilExit();
  if (result.launch) launch(result.launch, opts.dir, result.files);
  return result.code;
}
