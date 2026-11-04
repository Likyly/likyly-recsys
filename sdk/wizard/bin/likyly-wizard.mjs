#!/usr/bin/env node
// The published package runs the bundle (dist/, built by `npm run build`): no dependency to
// install, so `npx @likyly/wizard` starts instantly.
const [major] = process.versions.node.split(".").map(Number);
if (major < 22) {
  console.error(`@likyly/wizard needs Node.js 22 or newer (you have ${process.versions.node}).`);
  process.exit(1);
}
const { main } = await import("../dist/cli.mjs");
main().then((code) => process.exit(code), (err) => {
  console.error(err?.stack ?? err);
  process.exit(1);
});
