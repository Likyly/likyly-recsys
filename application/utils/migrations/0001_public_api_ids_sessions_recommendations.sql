-- 0001 - public API v2: string ids, anonymous sessions, richer events, recommendation traces.
--
-- Additive and idempotent (see migrate.py). Nothing here drops or rewrites existing data;
-- rolling the application back to the previous version leaves this schema perfectly usable
-- by it (the one caveat - interactions.user_id becoming nullable - is documented in
-- API_MIGRATION.md).

-- 1. Free-form properties on the catalog and on users ----------------------------------
ALTER TABLE products ADD COLUMN IF NOT EXISTS properties jsonb;
ALTER TABLE users ADD COLUMN IF NOT EXISTS properties jsonb;

-- 2. Interactions: anonymous sessions, idempotency, attribution, payload ----------------
ALTER TABLE interactions ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE interactions ADD COLUMN IF NOT EXISTS session_id varchar;
ALTER TABLE interactions ADD COLUMN IF NOT EXISTS event_id varchar;
ALTER TABLE interactions ADD COLUMN IF NOT EXISTS recommendation_id varchar;
ALTER TABLE interactions ADD COLUMN IF NOT EXISTS placement varchar;
ALTER TABLE interactions ADD COLUMN IF NOT EXISTS properties jsonb;

CREATE INDEX IF NOT EXISTS ix_interactions_client_event_time
    ON interactions (client_id, event_type, occurred_at);
CREATE INDEX IF NOT EXISTS ix_interactions_session
    ON interactions (client_id, product_type, session_id, occurred_at) WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_interactions_recommendation
    ON interactions (recommendation_id) WHERE recommendation_id IS NOT NULL;
-- Idempotency: one event_id per tenant. Partial, so the (many) events without one never conflict.
CREATE UNIQUE INDEX IF NOT EXISTS uq_interactions_event_id
    ON interactions (client_id, event_id) WHERE event_id IS NOT NULL;

-- 3. Public <-> internal id mapping ------------------------------------------------------
CREATE TABLE IF NOT EXISTS id_map (
    client_id integer NOT NULL REFERENCES clients (id),
    product_type varchar NOT NULL,
    kind varchar(8) NOT NULL,
    external_id varchar NOT NULL,
    internal_id integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, product_type, kind, external_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_id_map_internal
    ON id_map (client_id, product_type, kind, internal_id);

-- Backfill: every integer id already in use keeps meaning exactly what it did - its public
-- id is simply its decimal string. ON CONFLICT DO NOTHING makes re-runs harmless.
-- created_at is listed explicitly: on a real boot create_all runs BEFORE this file and creates id_map
-- with a NOT NULL created_at and no server default (the ORM supplies it), so this table's DEFAULT
-- below is not guaranteed to exist.
INSERT INTO id_map (client_id, product_type, kind, external_id, internal_id, created_at)
SELECT client_id, product_type, 'item', work_id::text, work_id, now()
FROM (
    SELECT client_id, product_type, work_id FROM products
    UNION
    SELECT client_id, product_type, work_id FROM interactions
) AS known_items
ON CONFLICT DO NOTHING;

INSERT INTO id_map (client_id, product_type, kind, external_id, internal_id, created_at)
SELECT client_id, product_type, 'user', user_id::text, user_id, now()
FROM (
    SELECT client_id, product_type, user_id FROM users
    UNION
    SELECT client_id, product_type, user_id FROM interactions WHERE user_id IS NOT NULL
) AS known_users
ON CONFLICT DO NOTHING;

-- 4. Recommendation traces (attribution of impressions / clicks / purchases) ---------------
CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id varchar PRIMARY KEY,
    client_id integer NOT NULL REFERENCES clients (id),
    product_type varchar NOT NULL,
    user_id varchar,
    session_id varchar,
    item_id varchar,
    placement varchar,
    strategy varchar NOT NULL,
    origin varchar(16) NOT NULL DEFAULT 'auto',
    item_ids jsonb NOT NULL,
    request_id varchar,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_recommendations_client_created
    ON recommendations (client_id, created_at);
CREATE INDEX IF NOT EXISTS ix_recommendations_client_placement
    ON recommendations (client_id, placement, created_at);

-- 5. Officially supported event types for tenants that already exist ----------------------
-- purchase/view are already seeded for everyone; only add the new ones, and never overwrite
-- a tenant's own definition of the same name (DO NOTHING).
INSERT INTO client_event_types (client_id, event_type, label, weight, created_at)
SELECT c.id, v.event_type, v.label, v.weight, now()
FROM clients c
CROSS JOIN (VALUES
    ('impression', 'Impression', 0.0::double precision),
    ('click', 'Clic', 0.2::double precision),
    ('add_to_cart', 'Ajout au panier', 1.0::double precision),
    ('remove_from_cart', 'Retrait du panier', 0.0::double precision)
) AS v (event_type, label, weight)
ON CONFLICT (client_id, event_type) DO NOTHING;
