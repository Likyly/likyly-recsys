-- 0002 - a workspace's display name, separate from its technical name.
--
-- clients.name is the technical identifier ("li_" + code) and stays as it is; the name an owner gives
-- their workspace from the dashboard now goes into display_name instead of overwriting it.
-- Additive and idempotent (see migrate.py): existing rows keep display_name NULL, and the console
-- falls back to name, so a workspace renamed before this change keeps showing the name it was given.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS display_name varchar;
