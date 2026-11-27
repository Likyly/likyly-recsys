// `npx @likyly/wizard pixel`, drawn: why a pixel, the code to copy (already in the clipboard),
// where to paste it in the Shopify admin, how to test. In a terminal: o opens the Shopify page,
// c copies the code again, q (or Enter) quits. Elsewhere it is drawn once.

import { useState } from "react";
import { Box, Text, render, useApp, useInput } from "ink";
import { C, Gradient } from "./theme.jsx";
import { KeyHints } from "./widgets.jsx";
import { copyToClipboard, openUrl } from "./system.mjs";

/** One line of the pixel: comments grey, strings in mint, the rest as is. */
function CodeLine({ line }) {
  if (line.trim().startsWith("//")) return <Text color={C.muted}>{line || " "}</Text>;
  const parts = line.split(/("(?:[^"\\]|\\.)*")/);
  return (
    <Text>
      {parts.map((part, i) =>
        i % 2 ? (
          <Text key={i} color={C.mint}>
            {part}
          </Text>
        ) : (
          <Text key={i}>{part}</Text>
        ),
      )}
    </Text>
  );
}

function Section({ n, title, note, children }) {
  return (
    <Box flexDirection="column" marginTop={1}>
      <Box gap={1}>
        <Text color="white" backgroundColor={C.blue} bold>
          {` ${n} `}
        </Text>
        <Text bold>{title}</Text>
        {note ? <Text color={C.muted}>· {note}</Text> : null}
      </Box>
      <Box flexDirection="column" paddingLeft={4}>
        {children}
      </Box>
    </Box>
  );
}

export function PixelScreen({ code, url, store, copied, interactive, onOpen, onCopy, onQuit }) {
  const [status, setStatus] = useState(null);
  useInput(
    (input, key) => {
      if (input === "o") setStatus(onOpen() ? "Shopify admin opened in your browser." : `Open it yourself: ${url}`);
      else if (input === "c") setStatus(onCopy() ? "Code copied again." : "No clipboard here: select the code above.");
      else if (input === "q" || key.return || key.escape) onQuit();
    },
    { isActive: interactive },
  );
  const lines = code.replace(/\n$/, "").split("\n");
  const storeUrl = store ? `https://${store}` : "https://<your-store>.myshopify.com";
  return (
    <Box flexDirection="column" paddingX={1}>
      <Box gap={1}>
        <Gradient bold>LIKYLY</Gradient>
        <Text bold>· purchases on Shopify</Text>
      </Box>
      <Text color={C.muted}>
        Shopify&apos;s checkout runs no theme code: only a pixel sees the add to cart and the order. Paste it once, and LIKYLY counts
        the revenue its recommendations bring.
      </Text>

      <Section n="1" title="Copy the pixel" note={copied ? "already in your clipboard" : "select it below"}>
        <Box flexDirection="column" borderStyle="round" borderColor={copied ? C.mint : C.amber} paddingX={1} marginTop={1}>
          {lines.map((line, i) => (
            <CodeLine key={i} line={line} />
          ))}
        </Box>
        <Text color={C.muted}>8 lines, your public key inside. The rest of the code lives at LIKYLY and stays up to date.</Text>
      </Section>

      <Section n="2" title="Paste it in the Shopify admin" note="once">
        <Text>
          <Text color={C.sky} underline>
            {url}
          </Text>
        </Text>
        <Text>
          <Text bold>Add custom pixel</Text>
          <Text color={C.muted}> → </Text>name <Text bold>LIKYLY</Text>
          <Text color={C.muted}> → </Text>replace the example code with yours
          <Text color={C.muted}> → </Text>
          <Text bold>Save</Text>
          <Text color={C.muted}> → </Text>
          <Text bold>Connect</Text>
        </Text>
        <Text>
          <Text color={C.amber} bold>
            ! Don&apos;t forget Connect
          </Text>
          <Text color={C.muted}>: until it is clicked, Shopify sends the pixel nothing (no add to cart, no purchase).</Text>
        </Text>
        <Text color={C.muted}>
          Customer privacy: &quot;Not required&quot; to test right away; &quot;Required&quot; sends only for visitors who accept analytics.
        </Text>
      </Section>

      <Section n="3" title="Test a purchase">
        <Text>
          From <Text color={C.sky}>{storeUrl}</Text> or the preview link of <Text bold>shopify theme dev</Text>
          <Text color={C.muted}> (not 127.0.0.1: the checkout runs on the store&apos;s address)</Text>
        </Text>
        <Text>
          Click a recommended product, buy it, then <Text bold>npx @likyly/wizard impact</Text> shows the purchase and its revenue.
        </Text>
      </Section>

      {status ? (
        <Box marginTop={1}>
          <Text color={C.mint}>✓ {status}</Text>
        </Box>
      ) : null}
      {interactive ? (
        <KeyHints
          hints={[
            ["o", "open the Shopify page"],
            ["c", "copy again"],
            ["q", "quit"],
          ]}
        />
      ) : null}
    </Box>
  );
}

/** Draws the pixel screen; in a terminal, waits for o / c / q. */
export async function showPixel({ code, url, store }) {
  const interactive = Boolean(process.stdin.isTTY && process.stdout.isTTY);
  const copied = interactive && copyToClipboard(code);
  let app;
  const quit = () => app?.unmount();
  app = render(
    <PixelScreen
      code={code}
      url={url}
      store={store}
      copied={copied}
      interactive={interactive}
      onOpen={() => (/^https?:/.test(url) ? openUrl(url) : false)}
      onCopy={() => copyToClipboard(code)}
      onQuit={quit}
    />,
    { exitOnCtrlC: true },
  );
  if (!interactive) app.unmount();
  await app.waitUntilExit();
}
