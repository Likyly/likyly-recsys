# LIKYLY SDK for {{name}}

The official {{name}} client for the [LIKYLY](https://likyly.com) recommendations API. Send your catalog and what your visitors do, get personalised recommendations back.

```
catalog (items)  →  events  →  recommendations  →  impression → click → purchase
```

## Install

```{{installFence}}
{{install}}
```

{{requires}}

## Quick start

{{context}}### Initialize

Create one client and reuse it. Use your **secret** key on your server (see [Which key?](#which-key)).

```{{fence}}
{{snippet:init}}
```

### Send your catalog

```{{fence}}
{{snippet:catalog}}
```

Ids are your own: any string (`SKU-123`, a UUID, `gid://shopify/Product/123`). `properties` is free-form; `category` and `description` improve similarity, everything else is stored as is.

### Track events

```{{fence}}
{{snippet:track}}
```

### Get recommendations

```{{fence}}
{{snippet:recommend}}
```

You never choose an algorithm: LIKYLY picks the best strategy for what it knows about the visitor and tells you which one it used (`strategy`: `hybrid`, `content`, `collaborative`, `session` or `popular`). With no user, no session and no item you still get a useful answer: what is popular.

### Close the loop

```{{fence}}
{{snippet:attribution}}
```

Send the recommendation id back on the events that follow (`impression` when it is displayed, `click`, `add_to_cart`, `purchase`) and LIKYLY can measure which recommendations actually convert.

## Which key?

| | Secret key | Public key |
|---|---|---|
| Where | your server only | anywhere, including a web page |
| Recommendations | yes | yes |
| Record events | yes | yes |
| Items and users (create, read, delete) | yes | no (`{{err:permissionDenied}}`) |
| `debug` explanations | yes | no |

The public key can only read recommendations and write events, so it is safe to expose. **Never put the secret key in code that ships to a browser or a mobile app.**

```{{fence}}
{{snippet:browser}}
```

## Events

`track` is the one mechanism; `impression`, `view`, `click`, `add_to_cart`, `remove_from_cart` and `purchase` are shortcuts that call it with the matching type. Event types are **open strings**: use your own (`favorite`, `share`, `video_played`, ...) as freely as the official ones.

```{{fence}}
{{snippet:custom}}
```

- An event needs an item and a user, a session, or both (both ties the anonymous session to the user once they log in).
- Anonymous visitors: generate a session id (any string) and keep it in a cookie; no login needed.
- `properties` is a free-form object (price, currency, order id, ...). Its keys are stored exactly as you send them and are never renamed by the SDK.
- `event_id` (idempotency key): replaying an event with the same id records nothing and returns `duplicate: true`. Set it on purchases, for example `purchase_<orderId>_<itemId>`.
- `placement` is a free-form label of where something happened or will be displayed (`homepage`, `product_page`, `cart`, ...).

## Advanced recommendations

`recommendations.get` is the normal way. When you want to pick the strategy yourself:

```{{fence}}
{{snippet:advanced}}
```

| Strategy | What it does |
|---|---|
| `popular` | The most popular items: the fallback for a visitor with no history |
| `similar` | Items similar to one item (content similarity), no user needed |
| `collaborative` | What users with similar histories liked. Needs a trained model, otherwise a not-found error |
| `hybrid` | Similar items personalised for a user; `alpha` (0 to 1) weighs collaborative against content |
| `session` | Recency-weighted, from `viewed item ids` or from a user's own view history (exactly one) |

These endpoints carry ids in the URL path, so ids containing `/` (such as `gid://shopify/Product/1`) or, for the session list, `,` are refused with a validation error: use `recommendations.get`, which takes ids in the request body.

## Errors

Errors are typed (every one is a `{{err:base}}`). Catch the specific ones you care about:

| Error | When |
|---|---|
| `{{err:validation}}` | The request is invalid: caught by the SDK before anything is sent, or a 422 from the API (the message names the offending fields) |
| `{{err:authentication}}` | 401: missing, invalid or revoked API key |
| `{{err:permissionDenied}}` | 403: for example a public key used for a secret-key operation, or a plan limit |
| `{{err:notFound}}` | 404 |
| `{{err:rateLimit}}` | 429: see the retry-after value |
| `{{err:api}}` | Any other error status from the API (the four rows above are its subtypes) |
| `{{err:network}}` | No HTTP response: DNS, connection reset, TLS |
| `{{err:timeout}}` | The request timed out |

Errors that come from the API carry {{errFields}}. Quote the request id when you contact support.

```{{fence}}
{{snippet:errors}}
```

## Retries and timeouts

The SDK retries transient failures for you (2 retries by default, exponential backoff from 0.5 s capped at 8 s, with full jitter):

- **429**: always retried, honouring `Retry-After`. A `Retry-After` longer than 60 s is not waited for: the rate-limit error is returned instead.
- **502, 503, 504, dropped connections and timeouts**: retried only when replaying the call is harmless: reads, upserts, deletes, events that carry an `event_id`, and batches where every event has one.
- An event **without** an `event_id` is never retried after an ambiguous failure, so a flaky network can never record a purchase twice. Set `event_id` on events you care about and they become safe to retry.
- Other 4xx errors are never retried.

## Configuration

| Setting | Option | Default |
|---|---|---|
{{configRows}}

```{{fence}}
{{snippet:config}}
```

{{perCall}}

## API reference

`key` says which API key may call it: **S** = secret key only, **P** = public or secret key.

| Method | What it does | Key |
|---|---|---|
{{methodRows}}

Lists are paginated with `limit` and `offset` (the API's default page is 100 items); the total is reported next to the page. Users have a batch `import` but no batch delete: the API has no such endpoint. `import` on items is an alias of the batch upsert (the CSV import is a dashboard feature).

## Notes for {{short}}

{{notes}}

## Documentation and support

- Documentation: <https://likyly.com/docs/sdk/{{slug}}>
- REST API reference (OpenAPI): <https://api.likyly.com/docs>
- Package: {{package}} on {{registry}}

MIT licensed.
