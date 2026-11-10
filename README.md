# LIKYLY Recsys API - product recommendations for any catalog, served via a multi-tenant REST API

![Python Version](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI Version](https://img.shields.io/badge/fastapi-0.115%2B-green)
![PostgreSQL](https://img.shields.io/badge/postgres-pgvector-blue)
![Implicit ALS](https://img.shields.io/badge/collaborative--filtering-implicit%20ALS-lightgrey)

## What is this project for?

![Rec Sys User Based](readmeAssets/ml-recsys-schema.jpg)

Amazon has sales that are 20% to 30% higher thanks to their product recommendation system!

Why not do it on your e-commerce website?

You can provide recommended products on a page of your website based on your own private data (e.g. your products,
users, ratings, purchases or page views).

It displays a relevant list of products both to anonymous visitors browsing a product page (content-based filtering,
including a no-login "session" mode from recently viewed items) and to logged-in users (collaborative filtering,
or a hybrid of both).

Whatever CMS (WordPress, Drupal, Prismic, Shopify...) or custom backend (Laravel, Symfony...) you currently use, this
project can be plugged to any source of data, and trains **your own** machine learning model per catalog - your
data never trains a shared/global model.

## Project Overview

This is a **multi-tenant** recommendation API: every request authenticates via an `X-API-Key` header scoped to a
specific client, whose catalog, events and trained models are fully isolated from every other client. Each client
can hold two keys - a secret key (full access, server-side only) and a restricted public key (recommendations +
interaction tracking only, safe to embed in client-side JS).

Recommendations combine:
- **Content-based filtering**: TF-IDF cosine similarity blended with pgvector semantic embeddings
  (sentence-transformers), computed at ingestion time and stored directly in Postgres - no separate vector
  database to run.
- **Collaborative filtering**: implicit ALS, with model versioning, promotion gating (a newly trained model only
  goes live if it beats the current production version) and MLflow experiment tracking.
- **Hybrid**: content-based and collaborative blended by a tunable `alpha`.
- **Session-based**: recommendations from a list of recently viewed product IDs, no `user_id` or login required.

Models are trained automatically once a client's catalog accumulates enough interactions from identified visitors
since the last training run (gated by the same promotion logic as a manual retrain, and by free-tier daily limits) -
that is also how a catalog gets its first model. Once one exists, it can be retrained on demand via the
`/generateModel` endpoint (`409` before that).

Legacy Pinecone-based vector endpoints are kept working for existing integrations but are frozen - superseded by
the pgvector integration above.

## Features

- **Multi-tenant by design**: catalog, events, models and usage are isolated per client via API key.
- **Self-service client provisioning**: a client is created automatically on first Supabase-authenticated call
  (`/clients/me`), with key rotation endpoints for both the secret and public key.
- **Admin endpoints**: list/inspect/rename/enable/disable clients, view daily usage, revoke or regenerate keys.
- **Catalog & event ingestion**: push product content profiles and purchase/view events per client.
- **Content-based, collaborative, hybrid and session recommendations** (see above).
- **Model lifecycle**: background training jobs with status polling, versioning, automatic promotion gating, and
  automatic retraining triggered by interaction volume rather than a blind cron.
- **Observability**: Prometheus metrics (`/metrics`) and Sentry error tracking (opt-in via env).
- **REST API**: built with FastAPI, interactive docs at `/docs`.

## Technologies Used

- **Python 3.11** - core language for the API and model training.
- **FastAPI** - the REST API framework, with OpenAPI docs.
- **PostgreSQL + pgvector** - catalog, events, clients, model versioning, and vector embeddings, all in one
  database.
- **sentence-transformers** - semantic embeddings for content-based recommendations.
- **implicit** - ALS-based collaborative filtering.
- **MLflow** - experiment tracking for model training runs.
- **Supabase** - authentication for self-service client provisioning.
- **Pinecone** - legacy vector index, frozen, kept working for existing integrations only.
- **Prometheus / Sentry** - metrics and error tracking.

## Key Endpoints

Vocabulary: **items** (your catalog), **users**, **events**, **recommendations**, **sessions**, **placements**. Ids are your own strings (`user_123`, `SKU-NIKE-001`, a UUID, a Shopify gid); integers are still accepted.

| Endpoint | Purpose |
|---|---|
| `POST /getRec` | **The** recommendation endpoint: send a user / session / item / viewed items, LIKYLY picks the strategy and returns `{recommendation_id, strategy, items}` |
| `PUT /items/{item_id}`, `GET /items`, `GET /items/{item_id}`, `DELETE /items/{item_id}` | Manage the catalog (free-form `properties`; secret key) |
| `POST /items/import`, `POST /items/delete` | Batch upsert / delete (JSON) |
| `PUT /users/{user_id}`, `GET /users`, `GET /users/{user_id}`, `DELETE /users/{user_id}`, `POST /users/import` | Manage user profiles (free-form `properties`) |
| `POST /events/{event_type}`, `POST /events/batch` | Track `impression`, `view`, `click`, `add_to_cart`, `remove_from_cart`, `purchase` or any type of your own - for users and anonymous sessions |
| `GET /getRec/{popular,content,collaborative,hybrid,session,sessionForUser}/...` | One endpoint per strategy, for expert use (`alpha` lives here) |
| `POST /clients/me` | Provision or fetch the caller's client (Supabase-authenticated) |
| `GET /generateModel` | Retrain a catalog's existing model (`409` while it has none) |
| `GET /models/versions`, `GET /models/status` | Model version history and current production status |
| `GET /analytics/{summary,placements,detail,items,plan}` | Recommendation performance and plan usage for an integration holding the secret key (or a developer key with `analytics:read`): totals, per-placement breakdown with attributed revenue and plan usage on every plan; daily trend and top items on Pro. Window clamped to the plan (Free 7 days, Pro 90) |

The `/products*` routes and the `/users{Purchases,Ratings,PageViews}` exports were removed. What changed and how to migrate: [API_MIGRATION.md](API_MIGRATION.md). The committed OpenAPI document is [docs/openapi.json](docs/openapi.json) (regenerate with `scripts/export_openapi.py`).

Full interactive documentation: `/docs` (Swagger UI) or `/openapi.json`.

![Rec Sys User Based](readmeAssets/ml-recsys-api-doc.jpg)

## SDKs and integrations

The API is consumed through official clients, all in the [Likyly/sdks](https://github.com/Likyly/sdks) monorepo
(released with release-please, one tag per package, versions in its `.release-please-manifest.json`):

| Language / platform | Package |
|---|---|
| TypeScript / JavaScript | `@likyly/sdk` (npm), React components `@likyly/react` |
| Python | `likyly` (PyPI) |
| PHP | `likyly/sdk` (Packagist), Laravel package `likyly/laravel` |
| .NET | `Likyly` (NuGet) |
| Java | `com.likyly:likyly-sdk` (Maven Central) |
| Go | `github.com/likyly/likyly-go` |
| Ruby | `likyly` (RubyGems) |
| Rust | `likyly` (crates.io) |
| Swift | `github.com/likyly/likyly-swift` (Swift Package Manager) |
| Android | `com.likyly:likyly-android` (Maven Central) |
| AI assistants | MCP server `@likyly/mcp-server` (npm) |

Also outside this repo: the WordPress / WooCommerce plugin ([Likyly/likyly-wordpress](https://github.com/Likyly/likyly-wordpress))
and the end-to-end tests of every integration ([Likyly/likyly-cms-sandboxes](https://github.com/Likyly/likyly-cms-sandboxes)).

The only client code left here is the install wizard, `sdk/wizard` (`npx @likyly/wizard`): it detects the framework
of a project, installs the right SDK and connects the coding assistant to the MCP server. Released by pushing a
`wizard-vX.Y.Z` tag (`.github/workflows/release-npm.yml`). The API contract the SDKs are tested against is
`docs/openapi.json`, mirrored in `Likyly/sdks/conformance/openapi.json` (a nightly check compares it with production).

## Running locally

```bash
pip install -r requirements.txt
cd application/api
python app.py
```

Then open `http://127.0.0.1:6061/docs`.

## Tests

The suite runs the real app against a real Postgres + pgvector - never the database in `application/api/.env`, which points at production:

```bash
docker run -d --name likyly-test-pg -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=likyly_test -p 54329:5432 pgvector/pgvector:pg16
pip install -r requirements-dev.txt
pytest            # the test database's public schema is dropped at session start
ruff check .
mypy application/api/ids.py application/api/observability.py application/api/schemas.py \
     application/api/ingestion.py application/api/recommender.py application/utils/migrate.py
```

Schema changes to already-deployed tables go in `application/utils/migrations/*.sql` (idempotent, additive); they are applied automatically at API start.
