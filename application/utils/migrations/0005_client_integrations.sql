-- 0005 - which SDK, CMS plugin or tool each workspace calls the API with.
--
-- One row per (workspace, agent): "typescript", "php", "laravel", "wordpress", "wizard", "mcp"...
-- recorded from the caller's User-Agent / X-Likyly-Client header (see app.py's
-- record_integration_middleware), for the admin back-office. No request body, no IP, no end-user data.
CREATE TABLE IF NOT EXISTS client_integrations (
    client_id INTEGER NOT NULL REFERENCES clients (id),
    agent VARCHAR NOT NULL,
    version VARCHAR,
    platform VARCHAR,
    user_agent VARCHAR,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, agent)
);
