// Bundle the CLI and its UI (Ink, React) into one ESM file, so `npx` has nothing to install.
import { build } from "esbuild";

// Ink only uses React DevTools when DEV=true: replace it with a no-op instead of shipping it.
const devtoolsStub = {
  name: "devtools-stub",
  setup(b) {
    b.onResolve({ filter: /^react-devtools-core$/ }, () => ({ path: "devtools-stub", namespace: "stub" }));
    b.onLoad({ filter: /.*/, namespace: "stub" }, () => ({ contents: "export default { initialize() {}, connectToDevTools() {} };" }));
  },
};

await build({
  entryPoints: ["src/cli.mjs"],
  outfile: "dist/cli.mjs",
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  jsx: "automatic",
  loader: { ".jsx": "jsx" },
  plugins: [devtoolsStub],
  define: { "process.env.NODE_ENV": '"production"' },
  // Some bundled dependencies are CommonJS and call require().
  banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" },
  legalComments: "none",
  minify: true,
  logLevel: "info",
});
