-- init.sql — Schema and menu for the South African food-ordering chatbot.
-- Demo orders are in seed_demo.sql (run it only on a demo database).
-- Bridges Dialogflow (ES/CX) with PostgreSQL persistence.
--
-- Idempotent: safe to run more than once against the same database.

-- ── Tables ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS food_items (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    price       NUMERIC(8, 2) NOT NULL CONSTRAINT food_items_price_nonneg CHECK (price >= 0),
    category    VARCHAR(50) DEFAULT 'Main',
    is_available BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        SERIAL PRIMARY KEY,
    customer_name   VARCHAR(120),
    customer_phone  VARCHAR(25),
    total_price     NUMERIC(10, 2) NOT NULL DEFAULT 0.00
                    CONSTRAINT orders_total_price_nonneg CHECK (total_price >= 0),
    order_status    VARCHAR(40) NOT NULL DEFAULT 'in progress'
                    CONSTRAINT orders_status_valid CHECK (order_status IN (
                        'in progress', 'preparing', 'out for delivery', 'delivered', 'cancelled'
                    )),
    delivery_address TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL REFERENCES food_items(id) ON DELETE RESTRICT,
    quantity    INTEGER NOT NULL DEFAULT 1 CONSTRAINT order_items_quantity_positive CHECK (quantity > 0),
    unit_price  NUMERIC(8, 2) NOT NULL CONSTRAINT order_items_unit_price_nonneg CHECK (unit_price >= 0),
    total_price NUMERIC(10, 2) NOT NULL CONSTRAINT order_items_total_price_nonneg CHECK (total_price >= 0),
    -- One line per item per order; `quantity` carries the count.
    CONSTRAINT order_items_order_item_unique UNIQUE (order_id, item_id)
);

CREATE TABLE IF NOT EXISTS conversation_logs (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(120) NOT NULL,
    user_message    TEXT NOT NULL,
    bot_response    TEXT,
    intent          VARCHAR(120),
    parameters      JSONB DEFAULT '{}'::jsonb,
    confidence      REAL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- food_items.name is already indexed by its UNIQUE constraint; lookups use lower(name).
CREATE INDEX IF NOT EXISTS idx_food_name_lower ON food_items(lower(name));
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(order_status);
-- order_items(order_id) is covered by UNIQUE (order_id, item_id); index item_id for the FK.
CREATE INDEX IF NOT EXISTS idx_order_items_item ON order_items(item_id);
CREATE INDEX IF NOT EXISTS idx_logs_session ON conversation_logs(session_id);

-- ── Triggers ────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_orders_updated_at ON orders;
CREATE TRIGGER trg_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ── Seed Data: South African Menu Items ────────────────────

INSERT INTO food_items (name, description, price, category) VALUES
    ('Bunny Chow',       'Hollowed-out loaf of bread filled with authentic Durban curry', 85.00, 'Main'),
    ('Boerewors Roll',   'Traditional grilled spiced South African sausage in a fresh roll with relish', 45.00, 'Street Food'),
    ('Pap & Chakalaka',  'Traditional maize porridge served with spicy tomato and bean relish', 35.00, 'Traditional'),
    ('Bobotie',          'Spiced minced meat baked with an egg-based topping and yellow rice', 75.00, 'Main'),
    ('Vetkoek',          'Golden deep-fried dough bread filled with savoury mince', 30.00, 'Street Food'),
    ('Kota',             'Township quarter-loaf filled with chips, polony, cheese, and atchar', 40.00, 'Street Food'),
    ('Malva Pudding',    'Sweet apricot jam sponge pudding drenched in warm cream sauce', 35.00, 'Dessert'),
    ('Milk Tart',        'Traditional South African dessert with sweet pastry and cinnamon custard (Melktert)', 30.00, 'Dessert'),
    ('Sosaties',         'Tender skewered marinated lamb and apricot skewers grilled over coals', 60.00, 'Braai / Grill')
ON CONFLICT (name) DO UPDATE SET
    price = EXCLUDED.price,
    description = EXCLUDED.description;
