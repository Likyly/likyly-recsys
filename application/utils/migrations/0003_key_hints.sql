-- 0003 - a non-secret preview of each API key, for the console.
--
-- Keys are stored as hashes only, so once shown they can't be displayed again. A hint (kind + first
-- characters, ellipsis, last four) lets the console show "sk_yVzt...gjP0" instead of nothing. Additive
-- and idempotent (see migrate.py): keys issued before this migration have no hint (NULL) until they
-- are regenerated, and the console says so rather than inventing one.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS secret_key_hint varchar;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS public_key_hint varchar;
-- developer_keys is created by the application (create_all), not by these migrations: on a database
-- that has not booted the app yet it does not exist, and will get the column from the model instead.
DO $$
BEGIN
    IF to_regclass('developer_keys') IS NOT NULL THEN
        ALTER TABLE developer_keys ADD COLUMN IF NOT EXISTS key_hint varchar;
    END IF;
END $$;
