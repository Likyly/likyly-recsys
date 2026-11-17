-- 0006 - keep the value of the site public keys.
--
-- A public key is public by design: it ships in a site's pages and can only read recommendations
-- and record events. Keeping it lets the console put it in the code it hands out (the Shopify
-- pixel, snippets) instead of asking the user to paste a key they already have. Secret and
-- developer keys stay hashed only. Additive and idempotent: keys made before this have no value.
DO $$
BEGIN
    IF to_regclass('public_keys') IS NOT NULL THEN
        ALTER TABLE public_keys ADD COLUMN IF NOT EXISTS key_value varchar;
    END IF;
END $$;
