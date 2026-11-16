// What the Shopify and Magento catalog steps show before asking for anything: a spinner while
// LIKYLY is asked for a store already connected, then either that store and a choice (keep it,
// new credentials, another store), or a short intro saying what the next fields are and why.

import { useEffect, useState } from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { C } from "./theme.jsx";
import { Select } from "./widgets.jsx";
import { describeSource, findConnectedSource } from "../catalog.mjs";

/** Runs the check once; `onResult(source | null)`. */
export function useConnectedSource({ apiKey, type, onResult }) {
  const [checking, setChecking] = useState(Boolean(apiKey));
  useEffect(() => {
    if (!apiKey) return;
    let live = true;
    findConnectedSource({ apiKey, type }).then((source) => {
      if (!live) return;
      setChecking(false);
      onResult(source);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once
  }, []);
  return checking;
}

export function Checking({ platform }) {
  return (
    <Text>
      <Text color={C.blue}>
        <Spinner type="dots" />
      </Text>{" "}
      Looking for a {platform} store already connected to LIKYLY…
    </Text>
  );
}

/**
 * The connected store and what to do with it. `standalone` (npx @likyly/wizard shopify|magento):
 * the developer came to change something, so "Give new credentials" comes first.
 */
export function ExistingSource({ platform, label, source, standalone, onChoice }) {
  const broken = source.status === "error";
  const items = [
    { value: "keep", label: "Keep it" },
    { value: "credentials", label: "Give new credentials", hint: label },
    { value: "another", label: "Connect another store" },
  ];
  if (standalone) items.unshift(items.splice(1, 1)[0]);
  return (
    <Box flexDirection="column">
      <Text>Your {platform} store is already connected to LIKYLY:</Text>
      <Box marginLeft={2} marginTop={1} flexDirection="column">
        <Text>
          <Text color={broken ? C.coral : C.mint}>{broken ? "✗" : "✓"}</Text> {describeSource(label, source)}
        </Text>
        {broken && source.last_error ? <Text color={C.coral}>{source.last_error}</Text> : null}
      </Box>
      <Box marginTop={1}>
        <Select items={items} onSubmit={onChoice} />
      </Box>
    </Box>
  );
}

/** The few lines above the first field when nothing is connected yet. */
export function Intro({ lines }) {
  return (
    <Box flexDirection="column" marginBottom={1}>
      {lines.map((l, i) => (
        <Text key={i} color={i ? C.muted : undefined}>
          {l}
        </Text>
      ))}
    </Box>
  );
}
