# @likyly/wizard

Install LIKYLY in a project with one command:

```bash
npx @likyly/wizard
```

An interactive terminal UI (built with [Ink](https://github.com/vadimdemedes/ink)) walks you through five steps:

1. **Detect your project**: the framework from the project's own files (`package.json`,
   `composer.json`, `pyproject.toml`, `Gemfile`, `go.mod`, `Cargo.toml`, `*.csproj`, `pom.xml`,
   `wp-config.php`) and the package manager already in use (npm, pnpm, yarn, bun, composer, uv,
   poetry, bundler…).
2. **Review the plan**: every command it will run and every file it will write, before anything
   happens. Nothing existing is overwritten.
3. **Install the SDK** (`@likyly/sdk`, plus `@likyly/react` for React frameworks), with the
   package manager's output streamed live. Bare `pip`, Maven/Gradle and WordPress are shown, not run.
4. **Connect your LIKYLY account**: opens likyly.com, where you sign in or create an account
   (email, GitHub or Google); the public key comes back to the wizard on its own (a form POST to
   a one-time server on `127.0.0.1`, guarded by a random `state`). You can also paste a key or
   skip. The key is written under the name the framework exposes to the browser
   (`NEXT_PUBLIC_LIKYLY_KEY`, `VITE_LIKYLY_KEY`, `PUBLIC_LIKYLY_KEY`…), plus a starter file (React
   provider, Nuxt plugin, SvelteKit module…). Secret (`sk_`) and developer (`lk_`) keys are refused.
5. **Connect your AI assistant** to the [LIKYLY MCP server](../mcp): `.mcp.json` (Claude Code, plus
   the skill in `.claude/skills/likyly`), `.cursor/mcp.json` (Cursor), `.vscode/mcp.json` (VS Code).
   The developer key is never written to these files: they reference `LIKYLY_DEVELOPER_KEY` (or VS
   Code asks for it on start).

It ends on a hand-off panel with the prompt to give your assistant (`c` copies it, `k` opens your
keys page).

## Commands

```bash
npx @likyly/wizard                 # full interactive install
npx @likyly/wizard mcp add         # only connect your AI assistant
npx @likyly/wizard detect          # print what was detected, change nothing
npx @likyly/wizard --yes --public-key pk_... --editor claude-code,cursor   # CI, no prompts
npx @likyly/wizard --dry-run       # show what would happen
npx @likyly/wizard --hosted        # hosted MCP (mcp.likyly.com) instead of npx
```

The terminal needs at least 64×20: below that the wizard asks you to enlarge the window and
resumes on its own. `LIKYLY_APP_URL` points the sign-in at another site (e.g.
`http://localhost:6001`), `BROWSER=none` prints the link instead of opening it.

With `--yes`, in CI, or when the output is piped, the wizard prints plain lines instead of the
interactive UI and exits non-zero when a step fails.

## Development

```bash
npm install
npm run build                                    # bundles src/ into dist/cli.mjs (esbuild)
npm test                                         # builds, then node:test
node bin/likyly-wizard.mjs --dry-run --dir ../../examples/demo-storefront
```

- `src/actions.mjs`: the steps, shared by both renderers
- `src/ui/`: the Ink UI (`app.jsx` flow, `widgets.jsx` controls, `theme.jsx` brand palette and logo)
- `src/plain.mjs`: the CI renderer

Ink and React are bundled into `dist/cli.mjs`, so the published package has no dependencies and
`npx` starts instantly. Requires Node.js 22+. The skill is copied from `docs/SKILL.md` at
`npm pack` time (`prepack`); in the monorepo it is read directly.

Before publishing: `@likyly/sdk`, `@likyly/react` and `@likyly/mcp-server` must be on npm (the
wizard installs them from the registry), and `mcp.likyly.com` deployed for `--hosted`.
