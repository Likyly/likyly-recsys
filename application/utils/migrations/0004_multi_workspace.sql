-- 0004 - multi-workspace: one account may own more than one workspace.
--
-- clients.supabase_user_id was UNIQUE (one Supabase account = exactly one workspace). Looked up
-- dynamically rather than by a hardcoded constraint name, since Postgres auto-generated whatever
-- that name is when the table was first created via create_all. Idempotent: a no-op once already
-- applied, and a no-op on a fresh database where the model (db.py's ClientModel) is already
-- non-unique. Replaced with a plain index - lookups by supabase_user_id stay fast now that a few
-- rows can share one value instead of at most one.
DO $$
DECLARE
    unique_constraint_name text;
BEGIN
    SELECT tc.constraint_name INTO unique_constraint_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
    WHERE tc.table_name = 'clients' AND tc.constraint_type = 'UNIQUE' AND kcu.column_name = 'supabase_user_id';

    IF unique_constraint_name IS NOT NULL THEN
        -- quote_ident + concatenation, not Postgres's format() with its identifier
        -- placeholder: psycopg2's driver-level execute() scans the raw SQL text for its
        -- own param placeholders and chokes on that character sequence even inside a
        -- comment or a quoted string, before the server ever parses it.
        EXECUTE 'ALTER TABLE clients DROP CONSTRAINT ' || quote_ident(unique_constraint_name);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_clients_supabase_user_id ON clients (supabase_user_id);
