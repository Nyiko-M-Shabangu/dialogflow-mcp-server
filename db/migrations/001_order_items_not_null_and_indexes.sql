-- 001_order_items_not_null_and_indexes.sql
--
-- Brings an EXISTING database (created from an older db/init.sql) in line with
-- the current schema. Fresh databases get the same result from db/init.sql.
--
-- Idempotent: safe to run more than once. Runs in one transaction; if any
-- order_items row has a NULL order_id or item_id it aborts without changing
-- anything (clean those rows up first).
--
-- Apply manually, e.g.:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/001_order_items_not_null_and_indexes.sql

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM order_items WHERE order_id IS NULL OR item_id IS NULL) THEN
        RAISE EXCEPTION 'order_items has rows with NULL order_id or item_id; fix them before applying this migration';
    END IF;
END $$;

-- Every order line must belong to an order and a menu item.
ALTER TABLE order_items ALTER COLUMN order_id SET NOT NULL;
ALTER TABLE order_items ALTER COLUMN item_id SET NOT NULL;

-- Name lookups compare lower(name); the plain name index is redundant with the
-- UNIQUE constraint's index.
CREATE INDEX IF NOT EXISTS idx_food_name_lower ON food_items (lower(name));
DROP INDEX IF EXISTS idx_food_name;

-- order_items(order_id) is covered by UNIQUE (order_id, item_id); item_id (the
-- other foreign key) had no index.
CREATE INDEX IF NOT EXISTS idx_order_items_item ON order_items (item_id);
DROP INDEX IF EXISTS idx_order_items_order;

COMMIT;
