# API migration guide

The existing LIKYLY API was evolved **in place** - same host, same auth, same routes for everything
that still exists. There is no `/v1` and no second API. This page lists what changed, what still
works, and what to do about it. The machine-readable contract is [docs/openapi.json](docs/openapi.json).

- [What's new](#whats-new)
- [Breaking changes and compatibility](#breaking-changes-and-compatibility)
- [Database migration](#database-migration)
- [Ids](#ids)
- [Events](#events)
- [Recommendations](#recommendations)
- [Public key vs secret key](#public-key-vs-secret-key)
- [Rate limits](#rate-limits)
- [Observability](#observability)
- [Not done / follow-ups](#not-done--follow-ups)

## What's new

| Area | Change |
|---|---|
| Vocabulary | `item`, `user`, `event`, `recommendation`, `session`, `placement`. `work_id` is gone from the public contract (kept as a deprecated alias). |
| Ids | Your own strings (`user_123`, `SKU-NIKE-001`, UUIDs, `gid://shopify/Product/123456`). Integers still accepted. |
| Recommendations | `POST /getRec` - one call, LIKYLY picks the strategy. Every call returns a `recommendation_id`. |
| Events | `impression`, `view`, `click`, `add_to_cart`, `remove_from_cart`, `purchase` out of the box; any other type is auto-registered. Anonymous sessions, `recommendation_id`, `placement`, free-form `properties`, `event_id` idempotency, `POST /events/batch`. |
| Items | `PUT/GET/DELETE /items/{item_id}`, `GET /items`, `POST /items/import`, `POST /items/delete`. Free-form `properties`. |
| Users | `PUT/GET/DELETE /users/{user_id}`, `GET /users`, `POST /users/import`. Free-form `properties`. |
| Attribution | Every recommendation is stored (ids only), so CTR / conversion / revenue per strategy and per placement are one SQL query away. |
| Ops | `X-Request-ID` on every response and error, structured JSON logs, per-route rate limits. |

## Breaking changes and compatibility

Two kinds of change: **removed routes** (a hard break - each has a direct replacement) and **contract
changes on routes that stay** (the old form is still accepted or still emitted alongside the new one).

### Removed routes

| Removed | Replacement |
|---|---|
| `GET /products` | `GET /items`, `GET /items/{item_id}` (secret key; paginated: `limit` default 100, max 1000, `offset`, total in `X-Total-Count`) |
| `PUT /products/{id}/profile` | `PUT /items/{item_id}` - body `{title, description, properties}`; `genre_1` → `properties.category` |
| `DELETE /products/{id}/profile` | `DELETE /items/{item_id}` |
| `GET /usersPurchases`, `/usersPageViews` | none - events are write-only from the outside. Record what you need in your own system, or query `interactions` in the database. |
| `GET /usersRatings` | none - it always returned an empty list |

### Contract changes on routes that stay

| # | Old contract | New contract | Compatibility | What to do |
|---|---|---|---|---|
| 1 | `work_id` (request bodies) | `item_id` | `work_id` accepted wherever `item_id` is (events, CSV imports); both sent and different → `422`. `viewed_work_ids` still works on `GET /getRec/session`. The strategy endpoints' array responses carry `item_id` *and* the deprecated `work_id` (an integer, present only when the id is a canonical integer, else `null`). | Read `item_id`. |
| 2 | Integer `user_id` / `product_id` / `work_id` | String ids | **Input:** integers accepted, stored as their decimal string (`5` and `"5"` are the same id). **Output:** ids are strings. Existing integer-id integrations need **no change** - their ids are backfilled as `"<int>"`. | Send your own ids as strings. |
| 3 | Recommendation endpoints return a bare array | `{recommendation_id, strategy, placement, items}` | `POST /getRec` returns the object. The strategy-specific `GET /getRec/*` endpoints **keep the array by default** and add the id in the `X-Recommendation-Id` / `X-Recommendation-Strategy` headers; `?response_format=object` returns the object. Deprecation proposed: default flips to `object` no sooner than 6 months after release, announced first. | New code: `POST /getRec`, or `response_format=object`. |
| 4 | `POST /events/{purchase,view,<type>}` require `user_id` and `work_id` (ints) | `item_id` required; `user_id` **or** `session_id` required | Strict superset: every old body is still valid. New optional fields: `session_id`, `recommendation_id`, `placement`, `properties`, `event_id`. `quantity` is now accepted (bounded `0..1,000,000`) on `/events/view` too. | Nothing. Add `session_id` for anonymous visitors, `recommendation_id` for attribution. |
| 5 | `GET /users` returned `user_gender/age/zip/firstname/lastname` (all required, `user_id` an integer) | `properties` (free-form), `user_id` a string | Old fields still returned (nullable, deprecated) and still writable. **Real output-type change:** `user_id` is `"5"` where it was `5`. Typed clients that declare it `int` must relax it. | Read `user_id` as a string. |
| 6 | Errors were `{"detail": ...}` | `{"detail": ..., "request_id": ...}` | Additive. | none |
| 7 | Unknown product on `/getRec/content` and `/getRec/hybrid` → `500` | `404` | Bug fix. | none |
| 8 | Path `count` unbounded | `1..500` | A `count` above 500 now returns `422`. | Use `POST /getRec` (max 100). |
| 9 | Items without a `description` were silently dropped from the similarity matrix, **shifting every following row onto the wrong item** | Kept (compared on title/category) | Bug fix - recommendations for catalogs with description-less items change (for the better). | none |
| 10 | Default event types: `purchase`, `view` | + `impression`, `click`, `add_to_cart`, `remove_from_cart` | Existing tenants get them via the migration, **without overwriting** a tenant's own definition of the same name. `impression` and `remove_from_cart` have weight 0: recorded, never counted as interest. | none |
| 11 | The **public key** could list and read the whole catalog (`GET /products`) and, on the collaborative endpoint, received other users' first/last names | The public key can only recommend and track events; `similar_users` is secret-key only | Deliberate tightening (see [Public key vs secret key](#public-key-vs-secret-key)). | Read the catalog server-side with the secret key. |
| 12 | `data_product_type` required on every route | **Optional** on every API-key route | Backward compatible: explicit always wins. Omitted → the account's only catalog (items *or* events), else `default` for a fresh account; several catalogs → `422` (the message deliberately doesn't list them - a public key must not learn catalog names). The list of an account's catalogs is cached 30 s, so right after creating a second catalog an omitted parameter may still resolve to the first for up to 30 s. The `/clients/me/*` routes (Supabase JWT) keep it required. | Omit it if you have one catalog. |
| 13 | `GET /models/versions` and `/models/status` returned the artifact's server `file_path` | Removed from the response | Internal path, never part of the contract. | none |

Not changed: authentication (`X-API-Key`, secret/public split), the `/generateModel` / `/models/*` routes,
the account and admin routes, the frozen Pinecone endpoints (`/getRec/contentVec/*`, still flagged deprecated).

**SDKs, the WordPress connector and the website were updated in the same change** to the new surface
(items/users/events/`getRecommendations`); the JS, Python, PHP and MCP SDKs are now `0.2.0`. Clients built
against the removed routes break until they move.

## Database migration

`application/utils/migrations/0001_public_api_ids_sessions_recommendations.sql`, applied by
`application/utils/migrate.py`. **The API applies pending migrations itself at start-up**
(`db.init_db()` → `create_all`, then `run_migrations`), inside one transaction, guarded by a Postgres
advisory lock so several workers starting together can't race. To run it by hand: `python migrate.py`
from `application/utils` with `DATABASE_URL` set.

What it does - all additive and idempotent (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`), nothing is
dropped or rewritten:

| Table | Change |
|---|---|
| `products`, `users` | `+ properties jsonb` (null on existing rows; read as synthesized from the old columns) |
| `interactions` | `user_id` **DROP NOT NULL** (anonymous sessions); `+ session_id, event_id, recommendation_id, placement, properties`; indexes `(client_id, event_type, occurred_at)`, `(client_id, product_type, session_id, occurred_at)` partial, `(recommendation_id)` partial, unique `(client_id, event_id)` partial |
| `id_map` (new) | `(client_id, product_type, kind, external_id) → internal_id`. PK is the tenant + external id lookup; unique `(client_id, product_type, kind, internal_id)` is the reverse. **Backfilled** from `products`, `users` and `interactions` with `external_id = internal_id::text`. |
| `recommendations` (new) | one row per recommendation call; indexes `(client_id, created_at)`, `(client_id, placement, created_at)` |
| `client_event_types` | inserts the four new official types for existing clients |
| `schema_migrations` (new) | applied versions |

Verified by `tests/test_migrations.py` against a real legacy schema (captured from the previous
`db.py`): row counts preserved, every legacy id backfilled, new ids never collide with legacy ones,
replaying the file is a no-op, and the resulting schema and indexes are **identical to a fresh install**.

**Production checklist**
1. Take a Supabase backup / point-in-time marker first (the migration is additive, but it is the first
   schema change ever applied automatically at boot).
2. The `vector` extension must already exist (it did - `products.embedding` uses it).
3. Deploy. The first boot runs the migration (seconds for current data volumes; the backfill is
   `INSERT ... SELECT DISTINCT` over `interactions` - on a very large table run `python migrate.py`
   once, beforehand, off-peak).
4. Old and new application versions can run against the migrated schema at the same time (rolling
   deploy safe).

**Rollback.** Redeploy the previous image; the extra columns and tables are ignored by it. One
caveat: once anonymous events exist (`interactions.user_id IS NULL`), the previous code's
`int(user_id)` reads would fail on them - delete or ignore those rows first
(`DELETE FROM interactions WHERE user_id IS NULL`). Do not drop the new columns unless you also
accept losing session/attribution data.

## Ids

Public ids are opaque strings, max 255 chars, no control characters. Internally the engine needs small
dense integers (the ALS matrix is indexed by them), so `id_map` allocates one per
`(tenant, catalog, kind)` on first sight - under an advisory lock, so concurrent first sightings never
collide (`tests/test_id_allocation.py`).

- An id is created **only by writes** (item/user upsert, an event). Recommendation requests never create
  mappings; an id LIKYLY has never seen just contributes no signal.
- Users and items have separate id spaces; catalogs (`data_product_type`) are separate namespaces, as before.
- Path segments can't contain `/` unescaped for every route: `/items/{item_id}` and `/users/{user_id}` accept
  it (`gid://shopify/Product/123456` works as-is); the legacy `/getRec/*/{product_id}/{count}` routes don't -
  use `POST /getRec`, which takes ids in the body.

## Events

```json
POST /events/{event_type}?data_product_type=shop
{
  "user_id": "user_123",
  "session_id": "sess_abc",
  "item_id": "item_456",
  "recommendation_id": "rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF",
  "placement": "homepage",
  "quantity": 1,
  "occurred_at": "2026-09-24T10:30:00Z",
  "properties": {"price": 129.90, "currency": "EUR", "order_id": "ORD-123"},
  "event_id": "evt_customer_1234"
}
```

- `item_id` required; **at least one of `user_id` / `session_id`** (`422` otherwise). Both together is
  supported and stored, so an anonymous history can later be attached to the user who logs in
  (`UPDATE interactions SET user_id = … WHERE session_id = … AND user_id IS NULL`). No automatic merge is
  implemented yet.
- `properties` is free-form JSON (≤ 16 KB), stored, never interpreted.
- `occurred_at` defaults to server time; a time without a zone is read as UTC.
- **Idempotency:** `event_id` is unique per account. A replay records nothing and returns
  `{"duplicate": true}` with `200`. Send it on purchases (`evt_<order_id>_<item_id>`); the
  `(client_id, event_id)` partial unique index is the guarantee, including inside one batch.
- **Batch:** `POST /events/batch` takes up to 1000 events, each with its own `event_type`. Validation is
  all-or-nothing (a malformed event gives a `422` naming its index, nothing is recorded).
- `/events/view` and `/events/purchase` are URL shortcuts for the generic route, sharing its code.
- Weights: `purchase` fort (3.0), `add_to_cart` moyen (1.0), `view` / `click` faible (0.2),
  `impression` / `remove_from_cart` aucun (0.0 - excluded from the model, from popularity and from the
  auto-retrain trigger, so showing an item never makes it look more popular). Tenants can retune tiers.
- Anonymous events feed popularity and session history; they are not in the user × item matrix.

## Recommendations

`POST /getRec?data_product_type=shop`

```json
{ "user_id": "user_123", "session_id": "sess_abc", "item_id": "item_456",
  "viewed_item_ids": ["item_001", "item_002"], "placement": "homepage", "count": 10 }
```
→
```json
{ "recommendation_id": "rec_01K5Z3W8Q9M2X7T4N6V1B0C8DF", "strategy": "hybrid", "placement": "homepage",
  "items": [{ "item_id": "item_981", "score": 0.934, "title": "…", "description": "…",
              "properties": {"category": "…"}, "explanation": {"reason": "…"} }] }
```

**Strategy selection** (`recommender.plan_strategies`, a pure function, unit-tested). It builds an ordered
plan and runs it until a step yields items; `popular` is always last:

| Signals | Plan |
|---|---|
| `user_id` + `item_id` | `hybrid` → `content` → … |
| `item_id` | `content` → … |
| `viewed_item_ids` | `session` → … |
| `user_id` with events + a trained model | `collaborative` → … |
| `user_id` / `session_id` with tracked views | `session` (LIKYLY's own history, last 10 views) → … |
| nothing | `popular` (topped up with catalog items when few have signal) |

A signal with no data behind it (unknown item, user with no events, no model yet, viewed ids not in the
catalog) is skipped, never an error. `alpha` is deliberately **not** on this endpoint; it stays on
`GET /getRec/hybrid/…`.

**Response fields**

| Field | Presence |
|---|---|
| `recommendation_id`, `strategy`, `items`, `items[].item_id` | always |
| `items[].score`, `title`, `description`, `properties`, `explanation.reason` | present; `score` is `null` only for catalog-order filler |
| `explanation` numeric signals (`content_similarity`, `semantic_similarity`, `collaborative_score`, `popularity_score`, `interaction_count`) | when the strategy computed them |
| `explanation.similar_users` | **debug only**: `debug: true` **and** the secret key (it points at other users) |
| `placement` | echo of the request |

**`recommendation_id`** is `rec_` + a 26-char ULID (time-sortable, no dependency; UUIDv4 wasn't chosen
because it isn't sortable). It is minted for every call (also the strategy-specific endpoints, in the
`X-Recommendation-Id` header), and stored in `recommendations` (ids only, in rank order - no item
content) by a background task, so a slow or failing write never affects the response.

Attribution query (tested):

```sql
SELECT r.strategy, r.placement,
       count(DISTINCT r.recommendation_id)                               AS recommendations,
       count(i.id) FILTER (WHERE i.event_type = 'impression')           AS impressions,
       count(i.id) FILTER (WHERE i.event_type = 'click')                AS clicks,
       count(i.id) FILTER (WHERE i.event_type = 'purchase')             AS purchases,
       sum((i.properties->>'revenue')::numeric) FILTER (WHERE i.event_type = 'purchase') AS revenue
FROM recommendations r
LEFT JOIN interactions i ON i.recommendation_id = r.recommendation_id AND i.client_id = r.client_id
WHERE r.client_id = :client_id
GROUP BY r.strategy, r.placement;
```

## Public key vs secret key

Unchanged architecture; the boundary is now pinned by tests and by `x-required-key` in the OpenAPI
(derived from each route's real auth dependency).

| Key | May |
|---|---|
| **public** (safe in a web page) | `POST /getRec`, `GET /getRec/*` (without `similar_users`), `POST /events/*` (incl. batch). **Nothing else** - it can neither write nor list/read the catalog. |
| **secret** | everything above **plus** catalog reads and writes, all `/users*`, `/generateModel`, `/models/*`, `similar_users` and `debug` |
| neither (Supabase JWT) | `/clients/me*`, `/admin/*` |

What a public key can still learn, by construction: it is embedded in web pages, so anyone can read it, and
`user_id` / `session_id` are not authenticated - so anyone holding it can ask for recommendations "as" a given
user (the recommended items reveal something about that user's history) or send fake events. That is inherent
to a browser-embeddable key; if user ids are guessable (sequential integers), have your backend proxy
recommendations for logged-in users with the secret key instead of exposing them to the browser.

Verified by `tests/test_security.py` and `tests/test_no_leaks.py` (public key refused everywhere it should be,
cross-tenant isolation, no server paths / SQL / exception text / personal data in any error body or log,
no credentials in the sources).

## Rate limits

Enforced at the APISIX gateway (`gateway/bootstrap-route.sh`), per client IP. **Applied on the production
droplet on 2026-09-24** and checked live (a 200-request burst on the catalog route: 52 served, 148 × `429`).
**Before:** one global limit (20 req/s, burst 10) for every route.

| Traffic | Paths | Rate | Burst |
|---|---|---|---|
| Recommendations | `/getRec`, `/getRec/*` | 100/s | 100 |
| Events | `/events/*` | 100/s | 200 |
| Catalog & users | `/items*`, `/users*`, `/products*`, `/users{Purchases,Ratings,PageViews}` | 20/s | 40 |
| Account & admin | `/clients/*`, `/admin/*` | 5/s | 10 |
| CSV import | `/clients/me/import/*` | 2/s | 5 |
| Everything else | `/*` (`/generateModel`, `/models/*`, docs) | 20/s | 10 |
| `/metrics` | answered `404` by the gateway | - | - |

Two defects found and fixed while applying this (both existed before this change):
- **The old limit was one bucket for the whole internet.** APISIX sits behind Caddy, so its `remote_addr` is always
  Caddy's container IP (`172.19.0.5` in every access-log line). The key is now `http_x_forwarded_for`; Caddy sets it
  to the real client IP and overwrites any value a client sends (a spoofed header was ignored - checked).
- **`/metrics` was public** (`200` from the internet). Prometheus scrapes `recsys-api:6061` directly, so the gateway
  now answers `404` for it.

There is no in-app limiter. Batch endpoints exist so server-side integrations don't need per-event requests.
Plan limits (unchanged): a free plan is capped at 50 items and 1 manual + 1 automatic training per day.

## Observability

- `X-Request-ID` on every response (yours is kept if it's 8-128 chars of `[A-Za-z0-9._:-]`, else a
  `req_<ULID>` is generated); also `request_id` in every error body, including unhandled `500`s.
- One JSON log line per request (method, **route template** - never the raw URL or query - status,
  duration) and per recommendation (`recommendation_id`, strategy, origin, placement, count, the plan
  attempted, whether user/session/item were present, `client_id`).
- Never logged: API keys, request bodies, user properties, raw ids (asserted by `tests/test_observability_and_ids.py`).
- Errors still go to Sentry when `SENTRY_DSN` is set; a failed recommendation-trace write is logged and reported.

## Not done / follow-ups

- **Demo client key rotation (do this first).** The website's demo shipped the demo client's **secret** key in
  `public/demo/app.js`, so it is public and in git history (commit `7b2392c`). The code no longer contains it, but
  the key is still valid: rotate it (`db.regenerate_secret_key(1)`, or the admin page), and generate a public key for
  the demo client. Put both in the website container's environment (`DEMO_RECSYS_SECRET_KEY`,
  `DEMO_RECSYS_PUBLIC_KEY` - see `likyly-website/.env.example`). Until then the demo pages that need them show an error.
- **Anonymous → user merge** is prepared (events carry both ids) but not implemented.
- **No reporting endpoint** for CTR / conversion / revenue - the data model supports it (see the query above).
- **SDKs still take the catalog as their first argument** (`productType` / `product_type`); they work with the optional parameter (pass `"default"`), but making it optional in their signatures is a separate, breaking change to their API.
- **Privacy/consent:** the WordPress connector and the JS SDK now keep an anonymous `session_id` in the visitor's
  `localStorage` (the SDK already kept a viewed-items list there). Depending on your consent policy that may need to
  go behind your cookie banner.
- **The legacy strategy routes are `async` but call blocking DB/CPU code** (pre-existing); the new endpoints are plain
  `def` so FastAPI runs them in its thread pool.
- **`GET /users` without `limit`** still returns every user (historical); `GET /items` paginates by default.
- **Production disk.** The droplet's root filesystem (`/dev/vda1`, 25 GB) was found **100 % full**. Docker's data
  lives on a separate 40 GB volume (`/mnt/volume_add_1`, 14 GB free), so image pulls are not blocked by it - but
  anything writing to `/` (logs, temp files, `~/.docker`) can fail. `/var/log` is 4.1 GB (1.4 GB of it the systemd
  journal) and `/usr` 2.5 GB; a `journalctl --vacuum-size` needs root, which the deploy user does not have. The
  `trivy-cache` volume sits on the *other* disk and is in use by the running `security-trivy-server` container, so
  deleting it would not help.
