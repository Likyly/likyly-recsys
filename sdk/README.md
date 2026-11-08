# SDKs

The LIKYLY SDKs (TypeScript, React, PHP, Laravel, Python, Java, .NET, Go, Ruby, Rust, Swift, Android) and the MCP
server live in their own public repository: **[Likyly/sdks](https://github.com/Likyly/sdks)**, with their history,
CI and release workflow.

Only the wizard stays here: [`wizard/`](wizard) (`@likyly/wizard` on npm, released by pushing a `wizard-vX.Y.Z`
tag, see `.github/workflows/release-npm.yml`). Its `prepack` copies `docs/SKILL.md` from this repository into the
package.

The API contract the SDKs are built on is `docs/openapi.json` (published at
`https://api.likyly.com/recsys-api/openapi.json`). When it changes, copy it to `conformance/openapi.json` in
Likyly/sdks; a weekly job there reports any difference. The SDKs' live tests need this repository:
`conformance/local-api.sh` in Likyly/sdks starts the API from a checkout of it (`LIKYLY_BACKEND`).
