# Event model

LIKYLY's event tracking was never limited to `view`/`purchase` - `POST /events/{event_type}`
already accepts any string, auto-registering one it hasn't seen with a default training
weight (see `db.DEFAULT_EVENT_TYPES`/`EVENT_TIER_WEIGHTS`). This page is the **public
vocabulary** a coding agent (or a human) should use, and the one thing that changed to support
it: clearer names for the events a recommendation-tracking integration needs, without
disturbing the training weights already tuned for the names that existed before.

## The five events

| Public name | Canonical type (stored as) | Weight tier | Scope |
|---|---|---|---|
| `product_view` | `view` | faible | catalog-wide |
| `recommendation_impression` | `impression` | aucun (never trains) | placement |
| `recommendation_click` | `click` | faible | placement |
| `add_to_cart` | `add_to_cart` | moyen | catalog-wide |
| `purchase` | `purchase` | fort | catalog-wide |

`recommendation_impression`/`recommendation_click`/`product_view` are **aliases**, not new
event types: `application/utils/db.py`'s `EVENT_TYPE_ALIASES`, applied in
`ingestion.record_events` before anything else runs, maps them onto the already-existing
`impression`/`click`/`view` types. This matters because introducing genuinely new type strings
would have them auto-register at the *default* tier (moyen) instead of inheriting the
already-correct, deliberately-chosen weight of the type they mean the same thing as -
`impression`'s weight is `aucun` specifically so that showing a recommendation never counts as
positive signal on its own (that would create a feedback loop: recommend X -> X gets shown ->
X looks more popular). Use either name anywhere in the API, the SDK, or the MCP tools - they
behave identically, including for training.

`add_to_cart` and `purchase` were already first-class event types before this - listed here
because they're part of the same funnel a placement's integration needs, not because anything
about them changed.

## Fields

Every event already carries what a recommendation-tracking integration needs - no schema
change was needed:

| Brief's term | Actual field | Notes |
|---|---|---|
| `tenant` | *(implicit)* | Resolved from the `X-API-Key` - never an explicit field, same as everywhere else in this API. |
| `placement_id` | `placement` | Free-form string - a Placement's `slug` when the event happened in that context. |
| `recommendation_request_id` | `recommendation_id` | The exact id `POST /placements/{slug}/recommend` (or `POST /getRec`) returned - what makes impression/click/purchase attribution possible. |
| `item_id` | `item_id` | Required. |
| `position` | `properties.position` | Not a first-class column - `properties` is the established free-form extension point (price, currency, order_id already live there); add `position` (the item's rank in the list you rendered) the same way. |
| `user_id` (optional) | `user_id` | |
| `anonymous_id` / `session_id` | `session_id` | One vocabulary: the SDK's `LikylySession`/`context.anonymousId` both ultimately send `session_id` on the wire - see `sdk/js`'s `LikylySession`. |
| `timestamp` | `occurred_at` | Optional; defaults to server time. |
| `metadata` | `properties` | Free-form, any additional keys. |

## Linking an anonymous session to a user (`POST /events/identify`)

`InteractionModel`'s own docstring anticipated this gap before it was built: "lets a later job
stitch an anonymous session's history onto the user once they log in - not implemented yet -
one UPDATE is all it takes." That UPDATE is now `db.link_session_to_user`, exposed as:

```
POST /events/identify
{"user_id": "user_123", "session_id": "sess_abc"}
-> {"linked_interactions": 4}
```

Every past interaction of `session_id` **not already attributed to a user** is reassigned to
`user_id` - a session that already belongs to a different user (a shared device) is left
alone, and calling it twice is harmless (nothing left to link the second time). Public-key-ok,
the same trust tier as tracking the events themselves - call it right after login/signup, from
the browser. `sdk/js`'s `LikylySession.identify(likyly, userId)` does this and remembers the
session locally in one call.

## Testing

`tests/test_events_aliases_and_identify.py` covers: each alias lands on its canonical type
(and never under the alias name), batch events are aliased too, `identify` links exactly the
unattributed rows, never reassigns an already-attributed session, is idempotent, and is
tenant-isolated.
