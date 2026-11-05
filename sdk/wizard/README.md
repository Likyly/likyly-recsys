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
   happens. Nothing existing is overwritten. In a git repository it offers to work on a new
   branch, `likyly/setup` (your uncommitted work comes along, untouched), and it never commits.
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

6. **Verify the installation**: SDK installed and its version, your stack supported (Next.js
   13+, React 18–19, Node 18+, ...), public and developer keys accepted by the API, assistants
   connected.

Before that, it lists what it changed in your project - apart from what you had already
modified - and what git does not track on purpose (`.env.local`, `.likyly/`). Review it in VS
Code's Source Control panel or with `git diff`; "Review the changes first" opens the diff.

It ends on one choice, Enter by default: **Launch Claude Code** on the integration prompt (or open
Cursor with the prompt copied). Your assistant then connects the catalog, creates the placement,
wires it into your pages and validates the integration - ask it in plain words ("check my LIKYLY
integration", "empty the LIKYLY cache"), there are no commands to learn.

In Claude Code the same are `/likyly-clear-cache [placement]` and `/likyly-uninstall`, plus
`/likyly-help` (the list of commands), installed with the skill: Claude Code runs the command
itself and shows its result. Two read-only reports come with them:

- `/likyly-inventory` - what LIKYLY added to the site: each recommendation block, the page it is
  on, how it is set up in LIKYLY and what it served in the last 24 h; the events the code sends;
  the setup files. It starts from `inventory` (below), then reads the code and asks LIKYLY.
- `/likyly-model [catalog] [days]` - the recommendation model: the version serving production
  (training date, precision@10, the data it learned from), the other versions and why they are
  not live, and what each engine served and earned on the site (MCP tool `get_model_overview`,
  API `GET /models/overview`, readable with the developer key).

`inventory` (also `--json`) lists the LIKYLY packages and versions, the key variable (never its
value), the setup files and every source line that mentions LIKYLY; it changes nothing. Uninstall shows the plan, then asks whether to proceed and on which
branch: a new `likyly/uninstall` branch (recommended) or the current one. When the wizard runs
from a local checkout instead of npm, the commands call that checkout.

`uninstall` removes the SDK packages, the public key line, the starter file (unless you edited
it), the `likyly` server from each assistant's MCP config, the skill, the commands and
`.likyly/`. It never edits your code: it lists the files that still use LIKYLY so your assistant
can take them out. Your LIKYLY account (catalog, placements, keys) is not touched.

The developer key comes back from the browser sign-in too and is stored in
`.likyly/credentials.env` (that folder ignores itself in git, file readable by you only); the
MCP server reads it there, so nothing needs exporting.

## Commands

```bash
npx @likyly/wizard                 # full interactive install
npx @likyly/wizard mcp add         # only connect your AI assistant
npx @likyly/wizard detect          # print what was detected, change nothing
npx @likyly/wizard cache clear     # empty LIKYLY's recommendation cache, every placement
npx @likyly/wizard cache clear pdp-related   # ... or one placement
npx @likyly/wizard uninstall       # remove what the wizard added (shows the plan, asks first; --dry-run to only look)
npx @likyly/wizard uninstall --yes --branch new   # ... without questions, on a new likyly/uninstall branch (or --branch current)
npx @likyly/wizard help            # the list of commands
npx @likyly/wizard --yes --public-key pk_... --editor claude-code,cursor   # CI, no prompts
npx @likyly/wizard --dry-run       # show what would happen
npx @likyly/wizard --hosted        # hosted MCP (mcp.likyly.com) instead of npx
```

The terminal needs at least 64×20: below that the wizard asks you to enlarge the window and
resumes on its own. `LIKYLY_APP_URL` points the sign-in at another site (e.g.
`http://localhost:6001`), `BROWSER=none` prints the link instead of opening it.

Testing against a local LIKYLY: `LIKYLY_API_URL` (e.g. `http://127.0.0.1:6061`) is used by the
verify step and written as the MCP server's `LIKYLY_BASE_URL`, and `LIKYLY_MCP_SERVER` (path to a
built `sdk/mcp/dist/index.js`) makes the MCP config run that file with `node` instead of the npm
package.

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
