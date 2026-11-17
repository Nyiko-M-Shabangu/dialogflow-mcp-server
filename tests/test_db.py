"""Integration tests against a real PostgreSQL.

Skipped unless TEST_DATABASE_URL is set, e.g.

    docker run -d --name df-mcp-pytest -e POSTGRES_DB=dialogflow_mcp \
        -e POSTGRES_USER=mcp -e POSTGRES_PASSWORD=x -p 55434:5432 postgres:17-alpine
    TEST_DATABASE_URL=postgresql://mcp:x@localhost:55434/dialogflow_mcp uv run pytest tests/test_db.py

WARNING: every test DROPs and recreates the `public` schema of that database.
Never point TEST_DATABASE_URL at a database you care about.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

if os.environ.get("REQUIRE_DB_TESTS") == "1" and not TEST_DATABASE_URL:
    pytest.fail("REQUIRE_DB_TESTS=1 but TEST_DATABASE_URL is empty", pytrace=False)

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set; skipping DB integration tests"
)

if TEST_DATABASE_URL:  # conftest already did this; repeat in case of direct import
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import psycopg  # noqa: E402
from tool_call import call  # noqa: E402

from dialogflow_mcp import database, server_es  # noqa: E402

DB_DIR = Path(__file__).resolve().parent.parent / "db"
INIT_SQL = (DB_DIR / "init.sql").read_text(encoding="utf-8")
SEED_SQL = (DB_DIR / "seed_demo.sql").read_text(encoding="utf-8")
SEED_ORDER_IDS = {1001, 1002, 1003, 1004}


# ── Fixtures ────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def _point_database_module_at_test_db():
    # The pool reads DATABASE_URL when it is first created.
    database.close_pool()
    original_env = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    yield
    database.close_pool()
    if original_env is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = original_env


@pytest.fixture(autouse=True)
def fresh_schema():
    """Recreate the schema from db/init.sql and db/seed_demo.sql before every test."""
    database.close_pool()
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute(INIT_SQL)
        conn.execute(SEED_SQL)
    yield
    database.close_pool()


def q1(sql: str, params: tuple | None = None):
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        return conn.execute(sql, params).fetchone()


def order_count() -> int:
    return q1("SELECT count(*) FROM orders")[0]


def order_item_count() -> int:
    return q1("SELECT count(*) FROM order_items")[0]


# ── Schema ──────────────────────────────────────────────────
def test_init_and_seed_sql_are_idempotent():
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(INIT_SQL)
        conn.execute(SEED_SQL)
    assert q1("SELECT count(*) FROM food_items")[0] == 9
    assert order_count() == 4
    assert order_item_count() == 7


def test_init_sql_alone_seeds_no_orders():
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute(INIT_SQL)
    assert q1("SELECT count(*) FROM food_items")[0] == 9
    assert order_count() == 0


def test_seed_customers_are_obviously_fictional():
    rows = database.fetch_all("SELECT customer_name, customer_phone, delivery_address FROM orders")
    for r in rows:
        assert r["customer_name"].startswith("Test Customer ")
        assert r["customer_phone"].startswith("+27 00 000 ")
        assert r["delivery_address"].endswith("Example Street, Testville")


def test_check_constraint_rejects_bad_status_at_db_level():
    with pytest.raises(psycopg.errors.CheckViolation):
        database.execute("UPDATE orders SET order_status = 'lost' WHERE order_id = 1001")
    # The pool rolled back and is still usable; the row is unchanged.
    row = database.fetch_one("SELECT order_status FROM orders WHERE order_id = %s", (1001,))
    assert row["order_status"] == "delivered"


def test_seeded_order_totals_match_line_items():
    rows = database.fetch_all(
        """SELECT o.order_id, o.total_price, SUM(oi.total_price) AS lines
           FROM orders o JOIN order_items oi USING (order_id)
           GROUP BY o.order_id, o.total_price ORDER BY o.order_id"""
    )
    assert {r["order_id"] for r in rows} == SEED_ORDER_IDS
    for r in rows:
        assert r["total_price"] == r["lines"]


# ── get_menu ────────────────────────────────────────────────
class TestGetMenu:
    def test_all(self):
        res = call(server_es.get_menu)
        assert res["total_items"] == 9
        cats = [i["category"] for i in res["menu"]]
        assert cats == sorted(cats)
        bunny = next(i for i in res["menu"] if i["name"] == "Bunny Chow")
        assert bunny["price"] == 85.0 and isinstance(bunny["price"], float)

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_blank_category_returns_all(self, blank):
        assert call(server_es.get_menu, blank)["total_items"] == 9

    def test_category_filter(self):
        res = call(server_es.get_menu, "Street Food")
        assert {i["name"] for i in res["menu"]} == {"Boerewors Roll", "Vetkoek", "Kota"}

    def test_category_case_insensitive_substring(self):
        assert {i["name"] for i in call(server_es.get_menu, "dess")["menu"]} == {
            "Malva Pudding",
            "Milk Tart",
        }

    def test_category_with_slash(self):
        assert [i["name"] for i in call(server_es.get_menu, "Braai / Grill")["menu"]] == [
            "Sosaties"
        ]

    @pytest.mark.parametrize("wild", ["%", "_", "%%", "M_in", "\\"])
    def test_category_wildcards_are_literal(self, wild):
        assert call(server_es.get_menu, wild)["total_items"] == 0

    def test_unavailable_items_hidden(self):
        database.execute("UPDATE food_items SET is_available = FALSE WHERE name = 'Kota'")
        names = {i["name"] for i in call(server_es.get_menu)["menu"]}
        assert "Kota" not in names and len(names) == 8


# ── get_food_item_details ───────────────────────────────────
class TestFoodItemDetails:
    def test_exact_case_insensitive(self):
        assert (
            call(server_es.get_food_item_details, "  bunny CHOW ")["item"]["name"] == "Bunny Chow"
        )

    def test_unique_substring(self):
        assert call(server_es.get_food_item_details, "boere")["item"]["name"] == "Boerewors Roll"

    def test_exact_beats_substring(self):
        database.execute(
            "INSERT INTO food_items (name, price, category) VALUES ('Milk', 10, 'Drinks')"
        )
        assert call(server_es.get_food_item_details, "milk")["item"]["name"] == "Milk"

    def test_ambiguous(self):
        res = call(server_es.get_food_item_details, "Bo")
        assert "ambiguous" in res["error"]
        assert "Bobotie" in res["error"] and "Boerewors Roll" in res["error"]

    def test_ambiguous_truncates_candidates(self):
        for i in range(12):
            database.execute(
                "INSERT INTO food_items (name, price) VALUES (%s, 1)", (f"Zz Item {i}",)
            )
        res = call(server_es.get_food_item_details, "Zz Item")
        assert "(and more)" in res["error"]

    def test_not_found(self):
        assert "not recognized" in call(server_es.get_food_item_details, "Pizza")["error"]

    @pytest.mark.parametrize("wild", ["%", "_", "%a%", "B_nny Chow"])
    def test_wildcards_literal(self, wild):
        assert "not recognized" in call(server_es.get_food_item_details, wild)["error"]

    @pytest.mark.parametrize("bad", ["", "   ", None, 5])
    def test_invalid_name(self, bad):
        assert "non-empty string" in call(server_es.get_food_item_details, bad)["error"]

    def test_unavailable_item_still_described(self):
        database.execute("UPDATE food_items SET is_available = FALSE WHERE name = 'Kota'")
        assert call(server_es.get_food_item_details, "Kota")["item"]["is_available"] is False


# ── complete_order ──────────────────────────────────────────
class TestCompleteOrder:
    def test_happy_path_totals(self):
        res = call(
            server_es.complete_order,
            "Lerato",
            "+27 70 000 0000",
            [{"name": "Bunny Chow", "quantity": 2}, {"name": "milk tart", "quantity": "1"}],
            "1 Main Rd",
        )
        assert res["success"] is True
        assert res["order_id"] > max(SEED_ORDER_IDS)
        assert res["total_price_zar"] == 200.0
        assert res["items"] == [
            {"name": "Bunny Chow", "quantity": 2, "unit_price": 85.0, "total_price": 170.0},
            {"name": "Milk Tart", "quantity": 1, "unit_price": 30.0, "total_price": 30.0},
        ]
        assert "R200.00" in res["message"]

        order = q1(
            "SELECT customer_name, total_price, order_status, delivery_address FROM orders WHERE order_id = %s",
            (res["order_id"],),
        )
        assert order == ("Lerato", Decimal("200.00"), "in progress", "1 Main Rd")
        lines = q1(
            "SELECT count(*), sum(total_price) FROM order_items WHERE order_id = %s",
            (res["order_id"],),
        )
        assert lines == (2, Decimal("200.00"))

    def test_default_quantity_and_address(self):
        res = call(server_es.complete_order, "A", "1", [{"name": "Kota"}])
        assert res["items"][0]["quantity"] == 1
        assert res["delivery_address"] == "Counter Collection"

    def test_duplicate_items_merged(self):
        res = call(
            server_es.complete_order,
            "A",
            "1",
            [{"name": "Kota", "quantity": 2}, {"name": " kota ", "quantity": 3}],
        )
        assert res["success"]
        assert res["items"] == [
            {"name": "Kota", "quantity": 5, "unit_price": 40.0, "total_price": 200.0}
        ]
        assert q1(
            "SELECT count(*), sum(quantity) FROM order_items WHERE order_id = %s",
            (res["order_id"],),
        ) == (1, 5)

    def test_merged_quantity_over_cap_rejected(self):
        before = (order_count(), order_item_count())
        res = call(
            server_es.complete_order,
            "A",
            "1",
            [{"name": "Kota", "quantity": 30}, {"name": "Kota", "quantity": 21}],
        )
        assert "exceeds the maximum of 50" in res["error"]
        assert (order_count(), order_item_count()) == before

    def test_merged_quantity_at_cap_accepted(self):
        res = call(
            server_es.complete_order,
            "A",
            "1",
            [{"name": "Kota", "quantity": 25}, {"name": "Kota", "quantity": 25}],
        )
        assert res["items"][0]["quantity"] == 50

    def test_unknown_item_leaves_no_rows(self):
        before = (order_count(), order_item_count())
        res = call(
            server_es.complete_order,
            "A",
            "1",
            [{"name": "Kota", "quantity": 1}, {"name": "Pizza", "quantity": 1}],
        )
        assert "not recognized" in res["error"]
        assert (order_count(), order_item_count()) == before

    def test_ambiguous_item_rejected(self):
        before = order_count()
        res = call(server_es.complete_order, "A", "1", [{"name": "Bo", "quantity": 1}])
        assert "ambiguous" in res["error"]
        assert order_count() == before

    def test_out_of_stock_rejected(self):
        database.execute("UPDATE food_items SET is_available = FALSE WHERE name = 'Kota'")
        before = order_count()
        res = call(server_es.complete_order, "A", "1", [{"name": "Kota", "quantity": 1}])
        assert "out of stock" in res["error"]
        assert order_count() == before

    def test_atomic_when_line_item_insert_fails(self, monkeypatch):
        """Fail on the (batched) order_items INSERT, after the order row was
        inserted: the order row must be rolled back."""
        real_transaction = database.transaction
        seen = {"line_inserts": 0}

        class FailingCursor:
            def __init__(self, cur):
                self._cur = cur

            def execute(self, sql, params=None):
                if "INSERT INTO order_items" in sql:
                    seen["line_inserts"] += 1
                    if seen["line_inserts"] == 1:
                        raise RuntimeError("injected failure mid-insert")
                return self._cur.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._cur, name)

        @contextmanager
        def failing_transaction():
            with real_transaction() as cur:
                yield FailingCursor(cur)

        monkeypatch.setattr(server_es, "transaction", failing_transaction)
        before = (order_count(), order_item_count())

        with pytest.raises(RuntimeError, match="injected"):
            call(
                server_es.complete_order,
                "Atomic Test",
                "1",
                [{"name": "Kota", "quantity": 1}, {"name": "Vetkoek", "quantity": 1}],
            )

        assert seen["line_inserts"] == 1  # the order row was inserted, then the lines failed
        assert (order_count(), order_item_count()) == before
        assert q1("SELECT count(*) FROM orders WHERE customer_name = 'Atomic Test'")[0] == 0
        # Pool still healthy afterwards.
        monkeypatch.setattr(server_es, "transaction", real_transaction)
        assert call(server_es.complete_order, "A", "1", [{"name": "Kota"}])["success"]

    @pytest.mark.parametrize(
        "name, phone", [("X" * 200, "1"), ("A", "9" * 40)], ids=["long-name", "long-phone"]
    )
    def test_oversized_fields_rejected_without_writing(self, name, phone):
        before = (order_count(), order_item_count())
        res = call(server_es.complete_order, name, phone, [{"name": "Kota", "quantity": 1}])
        assert "must be at most" in res["error"]
        assert (order_count(), order_item_count()) == before

    @pytest.mark.parametrize(
        "name,phone",
        [("X" * 200, "1"), ("A", "9" * 40), ("", "1"), ("A", None)],
    )
    def test_bad_customer_details_return_structured_error(self, name, phone):
        before = (order_count(), order_item_count())
        res = call(server_es.complete_order, name, phone, [{"name": "Kota", "quantity": 1}])
        assert "error" in res
        assert (order_count(), order_item_count()) == before


# ── track_order ─────────────────────────────────────────────
class TestTrackOrder:
    def test_seeded_order(self):
        res = call(server_es.track_order, 1001)
        assert res["order"]["order_status"] == "delivered"
        assert res["order"]["total_price"] == 115.0
        assert isinstance(res["order"]["created_at"], str)
        assert {i["item_name"] for i in res["items"]} == {"Bunny Chow", "Milk Tart"}

    def test_new_order_roundtrip(self):
        oid = call(server_es.complete_order, "A", "1", [{"name": "Bobotie", "quantity": 2}])[
            "order_id"
        ]
        res = call(server_es.track_order, oid)
        assert res["order"]["total_price"] == 150.0
        assert res["items"] == [
            {"quantity": 2, "unit_price": 75.0, "total_price": 150.0, "item_name": "Bobotie"}
        ]

    @pytest.mark.parametrize("admin", [True, False])
    def test_customer_pii_only_with_admin_tools(self, monkeypatch, admin):
        monkeypatch.setattr(server_es, "ADMIN_TOOLS_ENABLED", admin)
        order = call(server_es.track_order, 1001)["order"]
        for field in ("customer_name", "customer_phone", "delivery_address"):
            assert (field in order) is admin
        assert order["order_status"] == "delivered"

    def test_customer_pii_hidden_from_non_admin_caller(self, monkeypatch):
        # Admin tools on (MCP_ADMIN_TOKEN mode) but the caller is a user-token client.
        monkeypatch.setattr(server_es, "ADMIN_TOOLS_ENABLED", True)
        monkeypatch.setattr(server_es, "_admin_denied", lambda: {"error": "admin only"})
        order = call(server_es.track_order, 1001)["order"]
        for field in ("customer_name", "customer_phone", "delivery_address"):
            assert field not in order
        assert order["order_status"] == "delivered"

    @pytest.mark.parametrize("oid", [99999, 0, -1, 2**40])
    def test_not_found(self, oid):
        assert "not found" in call(server_es.track_order, oid)["error"]


# ── Admin tools (called directly; registration is tested in unit tests) ─
class TestDatabaseErrorsAreGeneric:
    """A driver error never reaches the client: one generic message, one log line."""

    GENERIC = {"error": server_es._DB_UNAVAILABLE_ERROR}

    def test_sql_error_text_not_returned(self, caplog):
        # Make the next queries fail with a real psycopg error (UndefinedTable),
        # whose text would name the table.
        database.execute("DROP TABLE order_items, food_items CASCADE")
        with caplog.at_level("WARNING", logger="dialogflow_mcp.server_es"):
            menu = call(server_es.get_menu)
            details = call(server_es.get_food_item_details, "Kota")
            placed = call(server_es.complete_order, "A", "1", [{"name": "Kota"}])
            tracked = call(server_es.track_order, 1001)
        for res in (menu, details, placed, tracked):
            assert res == self.GENERIC
            assert "food_items" not in str(res) and "relation" not in str(res)
        lines = [r.getMessage() for r in caplog.records if "database_error" in r.getMessage()]
        assert len(lines) == 4
        assert lines[0] == "database_error tool=get_menu error=UndefinedTable sqlstate=42P01"

    def test_pool_timeout_is_generic(self, monkeypatch):
        monkeypatch.setenv("DB_POOL_MAX", "1")
        monkeypatch.setenv("DB_POOL_TIMEOUT", "0.5")
        database.close_pool()
        try:
            with database.get_connection():  # hold the only connection
                res = call(server_es.update_order_status, 1001, "delivered")
        finally:
            database.close_pool()
        assert res == self.GENERIC


class TestUpdateOrderStatus:
    def test_valid_normalised(self):
        before = q1("SELECT updated_at FROM orders WHERE order_id = 1004")[0]
        res = call(server_es.update_order_status, 1004, "  Out For Delivery ")
        assert res == {
            "success": True,
            "order_id": 1004,
            "new_status": "out for delivery",
            "message": "Order #1004 updated to 'out for delivery'.",
        }
        status, updated = q1("SELECT order_status, updated_at FROM orders WHERE order_id = 1004")
        assert status == "out for delivery"
        assert updated > before

    @pytest.mark.parametrize("bad", ["lost", "", None, 3, "in-progress"])
    def test_invalid_status(self, bad):
        res = call(server_es.update_order_status, 1004, bad)
        assert "Invalid status" in res["error"]
        assert q1("SELECT order_status FROM orders WHERE order_id = 1004")[0] == "in progress"

    def test_missing_order(self):
        assert "not found" in call(server_es.update_order_status, 99999, "delivered")["error"]


class TestListLimits:
    @pytest.fixture()
    def many_orders(self):
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            conn.execute(
                """INSERT INTO orders (customer_name, total_price, order_status)
                   SELECT 'bulk ' || g, 1, 'preparing' FROM generate_series(1, 105) g"""
            )

    @pytest.mark.parametrize(
        "limit, expected",
        [(1000, 100), (100, 100), (5, 5), (0, 1), (-3, 1), ("abc", 10), (None, 10), ("7", 7)],
    )
    def test_recent_orders_clamped(self, many_orders, limit, expected):
        assert call(server_es.get_recent_orders, limit=limit)["count"] == expected

    def test_recent_orders_status_filter(self):
        res = call(server_es.get_recent_orders, status="Delivered")
        assert [o["order_id"] for o in res["orders"]] == [1001]

    def test_recent_orders_newest_first(self):
        ids = [o["order_id"] for o in call(server_es.get_recent_orders)["orders"]]
        assert ids == [1004, 1003, 1002, 1001]

    def test_recent_orders_status_filter_strips_whitespace(self):
        assert call(server_es.get_recent_orders, status=" delivered ")["count"] == 1

    def test_conversation_logs(self):
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            conn.execute(
                """INSERT INTO conversation_logs (session_id, user_message, created_at)
                   SELECT CASE WHEN g <= 3 THEN 's1' ELSE 's2' END, 'm' || g,
                          NOW() + g * INTERVAL '1 second'
                   FROM generate_series(1, 130) g"""
            )
        s1 = call(server_es.get_conversation_logs, session_id="s1")
        assert [r["user_message"] for r in s1["logs"]] == ["m1", "m2", "m3"]  # ascending
        assert call(server_es.get_conversation_logs, limit=10_000)["count"] == 100
        assert call(server_es.get_conversation_logs, limit=-1)["count"] == 1
        assert call(server_es.get_conversation_logs)["logs"][0]["user_message"] == "m130"


# ── detect_intent logging (Dialogflow client faked, DB real) ─
class TestDetectIntentLogging:
    @pytest.fixture()
    def fake_dialogflow(self, monkeypatch):
        from google.cloud import dialogflow_v2 as df

        calls = {}

        class FakeClient:
            def detect_intent(self, request):
                calls["session"] = request.session
                return df.DetectIntentResponse(
                    query_result=df.QueryResult(
                        fulfillment_text="Added 2 Kota.",
                        intent=df.Intent(display_name="order.add"),
                        intent_detection_confidence=0.875,
                        parameters={"food-item": ["Kota"], "number": [2]},
                    )
                )

        monkeypatch.setattr(server_es, "PROJECT", "test-proj")
        monkeypatch.setattr(server_es, "LOCATION", "global")
        monkeypatch.setattr(server_es, "ENVIRONMENT", "")
        monkeypatch.setattr(server_es, "_sessions_client", lambda: FakeClient())
        return calls

    def test_turn_is_logged(self, fake_dialogflow):
        res = call(server_es.detect_intent, "2 kota please", session_id="sess-1")
        assert fake_dialogflow["session"] == "projects/test-proj/agent/sessions/sess-1"
        assert res["intent"] == "order.add"
        assert res["fulfillment_text"] == "Added 2 Kota."
        logs = call(server_es.get_conversation_logs, session_id="sess-1")["logs"]
        assert len(logs) == 1
        assert logs[0]["user_message"] == "2 kota please"
        assert logs[0]["intent"] == "order.add"
        assert logs[0]["parameters"] == {"food-item": ["Kota"], "number": [2.0]}
        assert logs[0]["confidence"] == pytest.approx(0.875)

    def test_logging_failure_does_not_break_tool(self, fake_dialogflow):
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute("DROP TABLE conversation_logs")
        res = call(server_es.detect_intent, "hi", session_id="sess-2")
        assert res["intent"] == "order.add"
