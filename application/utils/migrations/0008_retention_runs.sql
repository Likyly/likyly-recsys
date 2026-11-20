-- 0008 - every run of the retention purge (retention.py): when, started by the daily thread or
-- an admin, how many events / recommendation traces / customers it deleted, or why it failed.
-- Read by the back-office's "Conservation des données" page.
CREATE TABLE IF NOT EXISTS retention_runs (
    id SERIAL PRIMARY KEY,
    trigger VARCHAR(16) NOT NULL,
    triggered_by VARCHAR,
    status VARCHAR(16) NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    interactions INTEGER,
    recommendations INTEGER,
    users INTEGER,
    error VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_retention_runs_started_at ON retention_runs (started_at);
