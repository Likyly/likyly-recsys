-- 0007 - indexes for the daily retention purge (retention.py): it looks for events and
-- recommendation traces older than 13 months across every workspace, which the existing
-- per-workspace indexes cannot serve.
CREATE INDEX IF NOT EXISTS ix_interactions_occurred_at ON interactions (occurred_at);
CREATE INDEX IF NOT EXISTS ix_recommendations_created_at ON recommendations (created_at);
