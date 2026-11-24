-- 0010 - a shop of a commerce platform tied to a workspace by that platform's LIKYLY app (today
-- the Shopify app: PUT /integrations/shopify). One shop -> one workspace, one workspace -> at
-- most one shop per platform. Shown in the console (GET /clients/me) and the back office.
CREATE TABLE IF NOT EXISTS platform_links (
    id SERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    platform VARCHAR(16) NOT NULL,
    external_id VARCHAR NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_links_shop ON platform_links (platform, external_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_links_client ON platform_links (client_id, platform);
