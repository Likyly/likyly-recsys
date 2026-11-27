-- 0011 - one-click connection of a shop by the LIKYLY app for Shopify: the merchant, signed in on
-- likyly.com, approves "tie this shop to my workspace" (platform_connect_requests, keyed by the
-- hash of a one-time state the app made); the app claims it with its own secret and gets a public
-- key and a secret key of its own. That secret key lives on the shop's link (platform_links.key_hash):
-- untying the shop revokes it, the workspace's own secret key is never handed out nor rotated.
ALTER TABLE platform_links ADD COLUMN IF NOT EXISTS key_hash VARCHAR;
ALTER TABLE platform_links ADD COLUMN IF NOT EXISTS key_hint VARCHAR;
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_links_key ON platform_links (key_hash) WHERE key_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS platform_connect_requests (
    id SERIAL PRIMARY KEY,
    platform VARCHAR(16) NOT NULL,
    state_hash VARCHAR NOT NULL UNIQUE,
    external_id VARCHAR NOT NULL,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ
);
