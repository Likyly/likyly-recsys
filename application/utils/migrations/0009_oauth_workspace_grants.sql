-- 0009 - the workspace an assistant connected through OAuth (Claude, ChatGPT, via mcp.likyly.com
-- and Supabase Auth's OAuth server) acts on, picked by the account on likyly.com/oauth/consent.
-- No row: the account's primary workspace. See db.OAuthWorkspaceGrantModel.
CREATE TABLE IF NOT EXISTS oauth_workspace_grants (
    supabase_user_id VARCHAR NOT NULL,
    oauth_client_id VARCHAR(64) NOT NULL,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (supabase_user_id, oauth_client_id)
);
CREATE INDEX IF NOT EXISTS ix_oauth_workspace_grants_client ON oauth_workspace_grants (client_id);
