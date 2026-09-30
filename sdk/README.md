# LIKYLY SDKs

Eight official SDKs over one public API (`items`, `users`, `events`, `recommendations`), each in its language's conventions.

| Language | Package | Directory |
|---|---|---|
| TypeScript / JavaScript | `@likyly/sdk` (npm) | [js](js) |
| PHP | `likyly/sdk` (Packagist) | [php](php) |
| Python | `likyly` (PyPI) | [python](python) |
| Java | `com.likyly:likyly-sdk` (Maven) | [java](java) |
| .NET | `Likyly` (NuGet) | [dotnet](dotnet) |
| Go | `github.com/likyly/likyly-go` | [go](go) |
| Ruby | `likyly` (RubyGems) | [ruby](ruby) |
| Rust | `likyly` (crates.io) | [rust](rust) |

Method names per language: [docs/METHODS.md](docs/METHODS.md). `mcp/` is the separate MCP server.

## How it is kept correct

- **Source of truth**: the OpenAPI document (`docs/openapi.json`, identical to `https://api.likyly.com/recsys-api/openapi.json`).
- **Shared scenarios** (`conformance/scenarios.json`): 43 request/response scenarios, validated against the OpenAPI by `conformance/validate_against_openapi.py` and replayed by every SDK's test suite.
- **Behaviour tests** per SDK: validation, error mapping, retry rules (an event without `event_id` is never retried), encoding.
- **Live tests** per SDK against a real API: `conformance/local-api.sh start`, then `source conformance/.local-api.env` and run the SDK's tests.
- **Documentation is generated**: `node docs/generate.mjs` builds every README, `docs/METHODS.md` and the website data from `docs/sdks.json` and the code samples in each `examples/quickstart*`, which `conformance/run-examples.sh` executes against a real API. `--check` fails on drift (CI).
