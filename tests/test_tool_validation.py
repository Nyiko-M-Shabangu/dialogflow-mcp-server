"""Tool input validation and bounds (no database, no network).

Covers detect_intent's session_id / text / language_code checks and its
Dialogflow error handling, order-id bounds on track_order and
update_order_status, and complete_order's entry caps, duplicate merging and
batched line inserts.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from tool_call import call

from dialogflow_mcp import server_es


def _boom(*_args, **_kwargs):
    raise AssertionError("must not be called")


# ── detect_intent input validation ────────────────────────────────────────
class TestDetectIntentValidation:
    @pytest.fixture(autouse=True)
    def _no_dialogflow(self, monkeypatch):
        monkeypatch.setattr(server_es, "_sessions_client", _boom)
        monkeypatch.setattr(server_es, "execute", _boom)

    @pytest.mark.parametrize(
        "sid",
        ["", "a" * 37, "has space", "semi;colon", "slash/inject", "../etc", "é-unicode", 123],
    )
    def test_rejects_bad_session_id(self, sid):
        res = call(server_es.detect_intent, "hello", session_id=sid)
        assert "Invalid session_id" in res["error"]

    @pytest.mark.parametrize(
        "text, expected",
        [("", "non-empty"), ("   ", "non-empty"), ("x" * 257, "at most 256"), (None, "non-empty")],
    )
    def test_rejects_bad_text(self, text, expected):
        assert expected in call(server_es.detect_intent, text)["error"]

    @pytest.mark.parametrize("lang", ["", "english", "en_US", "e", "en-", "en-US-x-toolongsubtag"])
    def test_rejects_bad_language_code(self, lang):
        assert "language_code" in call(server_es.detect_intent, "hi", language_code=lang)["error"]

    def test_accepts_valid_inputs(self, monkeypatch):
        calls = {}

        class FakeClient:
            def detect_intent(self, request):
                calls["session"] = request.session
                from google.cloud import dialogflow_v2 as df

                return df.DetectIntentResponse(
                    query_result=df.QueryResult(
                        fulfillment_text="Hi!", intent_detection_confidence=1.0
                    )
                )

        monkeypatch.setattr(server_es, "_sessions_client", lambda: FakeClient())
        monkeypatch.setattr(server_es, "execute", lambda *_a, **_k: 1)
        monkeypatch.setattr(server_es, "PROJECT", "demo-project")
        sid = "web-" + "a" * 32  # 36 characters, the backend's format
        res = call(server_es.detect_intent, "x" * 256, session_id=sid, language_code="en-US")
        assert "error" not in res
        assert res["session_id"] == sid
        assert calls["session"].endswith("/sessions/" + sid)

    def test_dialogflow_error_is_clean(self, monkeypatch):
        from google.api_core import exceptions as gexc

        class FailingClient:
            def detect_intent(self, request):
                raise gexc.ServiceUnavailable("backend said: secret-ish detail")

        monkeypatch.setattr(server_es, "_sessions_client", lambda: FailingClient())
        monkeypatch.setattr(server_es, "PROJECT", "demo-project")
        res = call(server_es.detect_intent, "hello", session_id="s1")
        assert res["error"].startswith("The language service is unavailable")
        assert res["upstream_error"] == "ServiceUnavailable"
        assert res["upstream_code"] == 503
        assert "secret-ish" not in json.dumps(res)


# ── order id bounds ────────────────────────────────────────────────────────
class TestOrderIdBounds:
    @pytest.mark.parametrize("oid", [0, -1, 2**31, 10**20])
    def test_out_of_range_track_order(self, monkeypatch, oid):
        monkeypatch.setattr(server_es, "fetch_one", _boom)
        assert "not found" in call(server_es.track_order, oid)["error"]

    @pytest.mark.parametrize("oid", [True, "1005", 1.5, None])
    def test_non_integer_track_order(self, monkeypatch, oid):
        monkeypatch.setattr(server_es, "fetch_one", _boom)
        assert "whole number" in call(server_es.track_order, oid)["error"]

    def test_max_int_reaches_db(self, monkeypatch):
        seen = {}

        def fake_fetch_one(_sql, params):
            seen["params"] = params
            return None

        monkeypatch.setattr(server_es, "fetch_one", fake_fetch_one)
        assert "not found" in call(server_es.track_order, server_es.MAX_ORDER_ID)["error"]
        assert seen["params"] == (server_es.MAX_ORDER_ID,)

    @pytest.mark.parametrize("oid", [2**31, 0, True])
    def test_update_order_status_bounds(self, monkeypatch, oid):
        monkeypatch.setattr(server_es, "execute", _boom)
        assert "error" in call(server_es.update_order_status, oid, "preparing")


# ── complete_order caps, pre-merge and batched insert ─────────────────────
MENU = {
    "kota": {"id": 6, "name": "Kota", "price": Decimal("40.00")},
    "vetkoek": {"id": 5, "name": "Vetkoek", "price": Decimal("30.00")},
}


def _fake_transaction(executed: list):
    class FakeCursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))

        def fetchall(self):
            sql, params = executed[-1]
            if "FROM food_items" in sql and "lower(name) = lower" in sql:
                row = MENU.get(params[0].strip().lower())
                return (
                    [{**row, "description": "", "category": "Main", "is_available": True}]
                    if row
                    else []
                )
            return []

        def fetchone(self):
            return {
                "order_id": 3001,
                "total_price": Decimal("0"),
                "order_status": "in progress",
                "created_at": datetime(2026, 11, 17, tzinfo=timezone.utc),
            }

    @contextmanager
    def fake_transaction():
        yield FakeCursor()

    return fake_transaction


class TestCompleteOrderBounds:
    def test_too_many_entries(self, monkeypatch):
        monkeypatch.setattr(server_es, "transaction", _boom)
        items = [{"name": "Kota", "quantity": 1}] * (server_es.MAX_ORDER_ENTRIES + 1)
        assert "at most 50 item entries" in call(server_es.complete_order, "A", "1", items)["error"]

    def test_too_many_distinct_items(self, monkeypatch):
        monkeypatch.setattr(server_es, "transaction", _boom)
        items = [{"name": f"Dish {i}", "quantity": 1} for i in range(server_es.MAX_ORDER_LINES + 1)]
        assert (
            "at most 20 different items" in call(server_es.complete_order, "A", "1", items)["error"]
        )

    def test_duplicates_premerged_and_lines_batched(self, monkeypatch):
        executed: list = []
        monkeypatch.setattr(server_es, "transaction", _fake_transaction(executed))
        res = call(
            server_es.complete_order,
            "Naledi",
            "+27000000001",
            [
                {"name": "Kota", "quantity": 1},
                {"name": " kota ", "quantity": 2},
                {"name": "Vetkoek", "quantity": 1},
                {"name": "KOTA", "quantity": 1},
            ],
        )
        assert res["success"] is True
        assert res["items"] == [
            {"name": "Kota", "quantity": 4, "unit_price": 40.0, "total_price": 160.0},
            {"name": "Vetkoek", "quantity": 1, "unit_price": 30.0, "total_price": 30.0},
        ]
        assert res["total_price_zar"] == 190.0
        # One exact-name lookup per DISTINCT name (2), not per entry (4).
        lookups = [s for s, _ in executed if "lower(name) = lower" in s]
        assert len(lookups) == 2
        # All lines in ONE insert statement with 5 params per line.
        line_inserts = [(s, p) for s, p in executed if "INSERT INTO order_items" in s]
        assert len(line_inserts) == 1
        sql, params = line_inserts[0]
        assert sql.count("(%s, %s, %s, %s, %s)") == 2
        assert params == (
            3001,
            6,
            4,
            Decimal("40.00"),
            Decimal("160.00"),
            3001,
            5,
            1,
            Decimal("30.00"),
            Decimal("30.00"),
        )

    def test_premerged_quantity_still_capped(self, monkeypatch):
        monkeypatch.setattr(server_es, "transaction", _fake_transaction([]))
        res = call(
            server_es.complete_order,
            "A",
            "1",
            [{"name": "Kota", "quantity": 30}, {"name": "kota", "quantity": 25}],
        )
        assert "exceeds the maximum of 50" in res["error"]
