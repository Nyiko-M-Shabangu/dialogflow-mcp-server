-- seed_demo.sql — Demo orders 1001-1004 for local development and tests.
--
-- Run after init.sql. The customers, phone numbers and addresses are fictional
-- placeholders. Do not run this on a database that holds real orders.
-- Idempotent: existing rows are left alone.

INSERT INTO orders (order_id, customer_name, customer_phone, total_price, order_status, delivery_address, created_at) VALUES
    (1001, 'Test Customer 1', '+27 00 000 0001', 115.00, 'delivered',        '1 Example Street, Testville', NOW() - INTERVAL '3 hours'),
    (1002, 'Test Customer 2', '+27 00 000 0002', 120.00, 'out for delivery', '2 Example Street, Testville', NOW() - INTERVAL '45 minutes'),
    (1003, 'Test Customer 3', '+27 00 000 0003', 85.00,  'preparing',        '3 Example Street, Testville', NOW() - INTERVAL '20 minutes'),
    (1004, 'Test Customer 4', '+27 00 000 0004', 70.00,  'in progress',      '4 Example Street, Testville', NOW() - INTERVAL '5 minutes')
ON CONFLICT (order_id) DO NOTHING;

-- Reset sequence to continue after manual IDs
SELECT setval('orders_order_id_seq', (SELECT MAX(order_id) FROM orders));

-- Resolve item_id and unit_price from the menu by name (no reliance on SERIAL ordering).
INSERT INTO order_items (order_id, item_id, quantity, unit_price, total_price)
SELECT v.order_id, fi.id, v.quantity, fi.price, fi.price * v.quantity
FROM (VALUES
    (1001, 'Bunny Chow',     1),
    (1001, 'Milk Tart',      1),
    (1002, 'Bobotie',        1),
    (1002, 'Boerewors Roll', 1),
    (1003, 'Bunny Chow',     1),
    (1004, 'Kota',           1),
    (1004, 'Vetkoek',        1)
) AS v(order_id, name, quantity)
JOIN food_items fi ON fi.name = v.name
ON CONFLICT (order_id, item_id) DO NOTHING;
