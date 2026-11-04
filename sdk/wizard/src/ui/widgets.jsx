import { useState } from "react";
import { Box, Text, useInput } from "ink";
import Spinner from "ink-spinner";
import TextInput from "ink-text-input";
import { C } from "./theme.jsx";

/* ── Timeline ──────────────────────────────────────────────────────────────── */

const ICON = {
  pending: ["◇", C.line],
  done: ["◆", C.mint],
  warn: ["◆", C.amber],
  fail: ["◆", C.coral],
  skipped: ["◇", C.muted],
};

/**
 * One step of the vertical timeline: icon, title, a short summary, and its body. `waiting`
 * means the step needs the developer (a steady blue marker), otherwise an active step spins.
 */
export function Step({ status, title, summary, last = false, waiting = false, children }) {
  const active = status === "active";
  const [icon, color] = ICON[status] ?? ICON.pending;
  const hasBody = Boolean(children) && (!Array.isArray(children) || children.some(Boolean));
  return (
    <Box flexDirection="column">
      <Box gap={1}>
        {active ? waiting ? <Text color={C.blue}>◈</Text> : <Text color={C.blue}><Spinner type="dots" /></Text> : <Text color={color}>{icon}</Text>}
        <Text bold={status !== "pending"} color={status === "pending" ? C.muted : active ? C.blue : undefined}>
          {title}
        </Text>
        {summary ? <Text color={C.muted}>· {summary}</Text> : null}
      </Box>
      <Box
        flexDirection="column"
        marginLeft={0}
        paddingLeft={2}
        paddingBottom={last ? 0 : 1}
        borderStyle="single"
        borderTop={false}
        borderRight={false}
        borderBottom={false}
        borderLeft={!last}
        borderColor={C.line}
        minHeight={last || hasBody ? 0 : 1}
      >
        {children}
      </Box>
    </Box>
  );
}

const MARK = { ok: ["✓", C.mint], info: ["·", C.muted], warn: ["!", C.amber], fail: ["✗", C.coral] };

/**
 * The results of a finished step. Amber is reserved for something the developer still has to
 * do (and says so: "To do"); "already in place" lines are neutral grey.
 */
export function Results({ items }) {
  return (
    <Box flexDirection="column">
      {items.map((r, i) => (
        <Box key={i} gap={1}>
          <Text color={MARK[r.level][1]} bold>
            {MARK[r.level][0]}
          </Text>
          <Text color={r.level === "info" ? C.muted : undefined}>
            {r.level === "warn" ? (
              <Text color={C.amber} bold>
                To do:{" "}
              </Text>
            ) : r.level === "fail" ? (
              <Text color={C.coral} bold>
                Error:{" "}
              </Text>
            ) : null}
            {r.text}
            {r.hint ? <Text color={r.level === "ok" || r.level === "info" ? C.muted : C.sky}> {r.hint}</Text> : null}
          </Text>
        </Box>
      ))}
    </Box>
  );
}

export function KeyHints({ hints }) {
  return (
    <Box marginTop={1} gap={2}>
      {hints.map(([k, label]) => (
        <Text key={k} color={C.muted}>
          <Text color={C.sky} bold>
            {k}
          </Text>{" "}
          {label}
        </Text>
      ))}
    </Box>
  );
}

/* ── Choices ───────────────────────────────────────────────────────────────── */

function useCursor(length, initial = 0) {
  const [i, setI] = useState(initial);
  const move = (d) => setI((v) => (v + d + length) % length);
  return [i, move];
}

/** Single choice: ↑/↓ (or j/k) to move, Enter to pick. */
export function Select({ items, onSubmit }) {
  const [i, move] = useCursor(items.length);
  useInput((input, key) => {
    if (key.upArrow || input === "k") move(-1);
    else if (key.downArrow || input === "j") move(1);
    else if (key.return) onSubmit(items[i].value);
  });
  return (
    <Box flexDirection="column">
      {items.map((it, k) => {
        const on = k === i;
        return (
          <Box key={it.value} gap={1}>
            <Text color={C.blue}>{on ? "❯" : " "}</Text>
            <Text bold={on} color={on ? undefined : C.muted}>
              {it.label}
            </Text>
            {it.hint ? <Text color={C.muted}>· {it.hint}</Text> : null}
          </Box>
        );
      })}
      <KeyHints
        hints={[
          ["↑↓", "move"],
          ["enter", "select"],
        ]}
      />
    </Box>
  );
}

/** Several choices: Space toggles, A toggles all, Enter confirms. */
export function MultiSelect({ items, initial = [], onSubmit }) {
  const [i, move] = useCursor(items.length);
  const [picked, setPicked] = useState(new Set(initial));
  const toggle = (v) =>
    setPicked((s) => {
      const n = new Set(s);
      n.has(v) ? n.delete(v) : n.add(v);
      return n;
    });
  useInput((input, key) => {
    if (key.upArrow || input === "k") move(-1);
    else if (key.downArrow || input === "j") move(1);
    else if (input === " ") toggle(items[i].value);
    else if (input === "a") setPicked((s) => (s.size === items.length ? new Set() : new Set(items.map((x) => x.value))));
    else if (key.return) onSubmit(items.filter((x) => picked.has(x.value)).map((x) => x.value));
  });
  return (
    <Box flexDirection="column">
      {items.map((it, k) => {
        const on = k === i;
        const checked = picked.has(it.value);
        return (
          <Box key={it.value} gap={1}>
            <Text color={C.blue}>{on ? "❯" : " "}</Text>
            <Text color={checked ? C.mint : C.line}>{checked ? "◉" : "○"}</Text>
            <Text bold={on} color={on || checked ? undefined : C.muted}>
              {it.label}
            </Text>
            {it.hint ? <Text color={C.muted}>· {it.hint}</Text> : null}
            {it.badge ? <Text color={C.violet}>{it.badge}</Text> : null}
          </Box>
        );
      })}
      <KeyHints
        hints={[
          ["space", "toggle"],
          ["a", "all"],
          ["enter", "confirm"],
        ]}
      />
    </Box>
  );
}

/* ── Public key ────────────────────────────────────────────────────────────── */

/**
 * Text field for the public key. Secret (`sk_`) and developer (`lk_`) keys are refused as they
 * are typed or pasted; Enter on an empty field skips the step.
 */
export function KeyInput({ isServerOnly, serverKeyMessage, keysUrl, onSubmit }) {
  const [value, setValue] = useState("");
  const v = value.trim();
  const refused = v && isServerOnly(v);
  const unusual = v && !refused && !v.startsWith("pk_");
  return (
    <Box flexDirection="column">
      <Text color={C.muted}>
        Find it at <Text color={C.sky}>{keysUrl}</Text>
      </Text>
      <Box marginTop={1} gap={1}>
        <Text color={refused ? C.coral : C.blue} bold>
          ›
        </Text>
        <TextInput
          value={value}
          onChange={setValue}
          placeholder="pk_live_…   (enter to skip)"
          onSubmit={() => {
            if (!refused) onSubmit(v);
          }}
        />
      </Box>
      {refused ? <Text color={C.coral}>✗ {serverKeyMessage}</Text> : null}
      {unusual ? <Text color={C.amber}>! Public keys usually start with pk_</Text> : null}
    </Box>
  );
}

/* ── Live command output ───────────────────────────────────────────────────── */

/** The last lines of a running command, in a dim frame. */
export function LiveLog({ command, lines, max = 6 }) {
  const tail = lines.slice(-max);
  return (
    <Box flexDirection="column">
      <Text>
        <Text color={C.muted}>$ </Text>
        <Text color={C.sky}>{command}</Text>
      </Text>
      <Box flexDirection="column" borderStyle="round" borderColor={C.line} paddingX={1} width={74}>
        {tail.length ? (
          tail.map((l, i) => (
            <Text key={i} color={C.muted} wrap="truncate-end">
              {l}
            </Text>
          ))
        ) : (
          <Text color={C.muted}>starting…</Text>
        )}
      </Box>
    </Box>
  );
}
