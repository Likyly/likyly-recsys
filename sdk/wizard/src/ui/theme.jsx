// The LIKYLY terminal identity: the brand palette, the wordmark and the four rays of the logo.
// Colors are mid-tone on purpose so they read on dark and light terminal themes alike.

import { Box, Text, useWindowSize } from "ink";

export const C = {
  blue: "#2F6BFF",
  sky: "#3F8CFF",
  violet: "#7758F6",
  mint: "#20C6A5",
  coral: "#FF5B72",
  amber: "#F6A92A",
  muted: "#8A97AD",
  line: "#3A4A6B",
};

const hexToRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const rgbToHex = (rgb) => `#${rgb.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("")}`;

/** Color at position t (0..1) along a multi-stop gradient. */
export function gradientAt(stops, t) {
  const x = Math.min(Math.max(t, 0), 1) * (stops.length - 1);
  const i = Math.min(Math.floor(x), stops.length - 2);
  const [a, b] = [hexToRgb(stops[i]), hexToRgb(stops[i + 1])];
  return rgbToHex(a.map((v, k) => v + (b[k] - v) * (x - i)));
}

/** Text painted with the brand gradient, character by character. */
export function Gradient({ children, stops = [C.blue, C.violet, C.coral], bold = false }) {
  const chars = [...String(children)];
  return (
    <Text bold={bold}>
      {chars.map((ch, i) => (
        <Text key={i} color={gradientAt(stops, chars.length > 1 ? i / (chars.length - 1) : 0)}>
          {ch}
        </Text>
      ))}
    </Text>
  );
}

// The big logo: LIKYLY in full blocks (no half blocks, which leave gaps in some terminal
// fonts), then the four rays of the logo fanning out, as 2-character pixels.
const GLYPHS = {
  L: ["██    ", "██    ", "██    ", "██    ", "██████"],
  I: ["██", "██", "██", "██", "██"],
  K: ["██  ██", "██ ██ ", "████  ", "██ ██ ", "██  ██"],
  Y: ["██  ██", "██  ██", " ████ ", "  ██  ", "  ██  "],
};
const ROWS = [0, 1, 2, 3, 4].map((r) => [..."LIKYLY"].map((l) => GLYPHS[l][r]).join(" "));
const RAY_COLORS = { A: C.amber, R: C.coral, B: C.blue, M: C.mint };
const RAYS = [".A..RR.", "A.RR...", "..BBBBB", ".MM....", "...MM.."];

/** The compact wordmark, for narrow terminals. */
export function Wordmark({ version }) {
  const letters = [..."LIKYLY"];
  return (
    <Box flexDirection="column">
      <Box gap={2}>
        <Text bold>
          {letters.map((l, i) => (
            <Text key={i} color={gradientAt([C.blue, C.violet], i / (letters.length - 1))}>
              {i ? ` ${l}` : l}
            </Text>
          ))}
        </Text>
        <Text color={C.muted}>wizard v{version}</Text>
      </Box>
      <Text>
        {[C.amber, C.coral, C.blue, C.mint].map((c) => (
          <Text key={c} color={c}>
            {"━━━"}
          </Text>
        ))}
      </Text>
    </Box>
  );
}

export const LOGO_WIDTH = ROWS[0].length + 2 + RAYS[0].length * 2;

export function Logo({ version }) {
  const { columns } = useWindowSize();
  if (columns < LOGO_WIDTH + 4) return <Wordmark version={version} />;
  const width = ROWS[0].length;
  return (
    <Box flexDirection="column">
      {ROWS.map((row, r) => (
        <Text key={r}>
          {[...row].map((ch, i) => (
            <Text key={i} color={gradientAt([C.blue, C.violet], i / (width - 1))}>
              {ch}
            </Text>
          ))}
          {"  "}
          {[...RAYS[r]].map((px, i) => (
            <Text key={`r${i}`} color={RAY_COLORS[px]}>
              {px === "." ? "  " : "██"}
            </Text>
          ))}
          {r === 4 ? <Text color={C.muted}>{"   "}wizard v{version}</Text> : null}
        </Text>
      ))}
    </Box>
  );
}

/** A small rounded tag, e.g. DRY RUN. */
export function Tag({ color, children }) {
  return (
    <Text backgroundColor={color} color="#071A3D" bold>
      {` ${children} `}
    </Text>
  );
}
