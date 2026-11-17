# ADR 0002: Redact track_order with an allowlist for non-admin callers

- Status: Accepted
- Date: 2026-11-17

## Context

Order IDs are sequential, so any user-token holder can enumerate orders with `track_order`. For callers who may not use admin tools, `track_order` removed `customer_name`, `customer_phone` and `delivery_address` from the row with `pop` before this decision. That is a blocklist. If someone later adds a column to the `SELECT` (an email, delivery notes, a loyalty number), it reaches every user-token caller without anyone deciding it should.

Customer names, phone numbers and addresses are personal information. Data-protection law (for example POPIA in South Africa) expects reasonable safeguards for it.

Alternatives considered:

- Keep the blocklist and add a test that pins the `SELECT` column list. Every schema change still depends on someone remembering the redaction list.
- Two SQL queries, one per role. This duplicates SQL and moves the role decision into query selection, which is harder to test without a database.

## Decision

Use an allowlist. Non-admin callers receive only `order_id`, `total_price`, `order_status`, `created_at` and `updated_at`, plus the line items. The allowlist is a module constant (for example `_PUBLIC_ORDER_FIELDS`) applied with a dict comprehension. It can be tested without a database by mocking `fetch_one` to return an extra, unknown column and asserting that it is absent.

## Consequences

- Non-admin output keys are exactly the allowlist. New columns stay hidden from non-admins until someone adds them on purpose.
- Admin output is unchanged.
- The non-admin response shape did not change: the same five order keys remain.
- To reverse: replace the comprehension with a loop that removes `_CUSTOMER_PII_FIELDS`.

Implementation notes: built in [`server_es.py`](../../src/dialogflow_mcp/server_es.py) as `_PUBLIC_ORDER_FIELDS` and a dict comprehension in `_track_order`. Whether to hide the fields is still decided on the event loop (`not ADMIN_TOOLS_ENABLED or _admin_denied() is not None`). Tests: unit tests with an extra, unknown column (absent for non-admins, present for admins) in [`tests/test_admin_gate.py`](../../tests/test_admin_gate.py); a real-database test in `tests/test_db.py`; and HTTP tests in [`tests/test_remote_auth.py`](../../tests/test_remote_auth.py) that call `track_order` with the user token and the admin token on Streamable HTTP and SSE and compare the returned keys.
