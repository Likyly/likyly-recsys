// "Connect your LIKYLY account": sign in or create an account in the browser (the public key
// comes back on its own), paste a key, or skip for now.

import { useEffect, useState } from "react";
import { Box, Text, useInput } from "ink";
import Spinner from "ink-spinner";
import { startBrowserAuth } from "../auth.mjs";
import { isServerOnlyKey } from "../setup.mjs";
import { KEYS_URL, SERVER_KEY_MESSAGE } from "../constants.mjs";
import { C } from "./theme.jsx";
import { KeyHints, KeyInput, Select } from "./widgets.jsx";
import { copyToClipboard, openUrl } from "./system.mjs";

function WaitForBrowser({ project, dir, onKey, onPaste, onSkip }) {
  const [auth, setAuth] = useState(null);
  const [error, setError] = useState(null);
  const [opened, setOpened] = useState(true);
  const [copied, setCopied] = useState(null);

  useEffect(() => {
    let live = true;
    let handle;
    startBrowserAuth({ project, dir }).then(
      (h) => {
        handle = h;
        if (!live) return h.close();
        setAuth(h);
        setOpened(openUrl(h.url));
        h.result.then((r) => {
          if (!live) return;
          h.close();
          if (r.key) onKey(r.key, { workspace: r.workspace, devKey: r.devKey });
          else if (r.manual) onPaste(r.devKey);
          else setError("No answer from the browser after 15 minutes.");
        });
      },
      (err) => setError(`Could not start the local sign-in server: ${err.message}`),
    );
    return () => {
      live = false;
      handle?.close();
    };
  }, []);

  useInput((input, key) => {
    if (input === "p") onPaste(null);
    else if (input === "o" && auth) setOpened(openUrl(auth.url));
    else if (input === "c" && auth) setCopied(copyToClipboard(auth.url));
    else if (key.escape || input === "s") onSkip();
  });

  return (
    <Box flexDirection="column">
      {error ? (
        <Text color={C.coral}>✗ {error}</Text>
      ) : (
        <Text>
          <Text color={C.blue}>
            <Spinner type="dots" />
          </Text>{" "}
          Waiting for you in the browser<Text color={C.muted}> · sign in or create your account, the key comes back here</Text>
        </Text>
      )}
      {auth ? (
        <Box marginTop={1} flexDirection="column">
          <Text color={C.muted}>{opened ? "Browser didn't open? Go to:" : "Open this page to continue:"}</Text>
          <Text color={C.sky}>{auth.url}</Text>
          {copied === true ? <Text color={C.mint}>Link copied ✓</Text> : copied === false ? <Text color={C.amber}>No clipboard tool found: select the link above</Text> : null}
        </Box>
      ) : null}
      <KeyHints
        hints={[
          ["o", "open again"],
          ["c", "copy link"],
          ["p", "paste a key instead"],
          ["esc", "skip"],
        ]}
      />
    </Box>
  );
}

/** Calls `onDone(publicKey | "", { workspace?, devKey? })`. */
export function AccountStep({ project, dir, onDone }) {
  const [mode, setMode] = useState("choose");
  const [devKey, setDevKey] = useState(null);
  if (mode === "browser")
    return (
      <WaitForBrowser
        project={project}
        dir={dir}
        onKey={(k, extra) => onDone(k, extra)}
        onPaste={(dk) => {
          setDevKey(dk);
          setMode("paste");
        }}
        onSkip={() => onDone("", {})}
      />
    );
  if (mode === "paste")
    return (
      <Box flexDirection="column">
        <KeyInput isServerOnly={isServerOnlyKey} serverKeyMessage={SERVER_KEY_MESSAGE} keysUrl={KEYS_URL} onSubmit={(k) => onDone(k, { devKey })} />
      </Box>
    );
  return (
    <Box flexDirection="column">
      <Text color={C.muted}>Your public key lets your site ask LIKYLY for recommendations.</Text>
      <Box marginTop={1}>
        <Select
          items={[
            { label: "Sign in or create an account", value: "browser", hint: "opens likyly.com, the key comes back on its own" },
            { label: "I already have my public key", value: "paste", hint: "paste it here" },
            { label: "Skip for now", value: "skip" },
          ]}
          onSubmit={(v) => (v === "skip" ? onDone("", {}) : setMode(v))}
        />
      </Box>
    </Box>
  );
}
