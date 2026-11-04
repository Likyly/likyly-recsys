#!/usr/bin/env node
import { main } from "../src/cli.mjs";

main().then((code) => process.exit(code), (err) => {
  console.error(err?.stack ?? err);
  process.exit(1);
});
