"""The admin gate, the tool inventory, the track_order allowlist and generic
database errors (no database, no network).

`server_es._admin_denied()` decides from the MCP request context. These tests
replace `mcp.get_context` with fakes, so they cover every branch without a
running server; tests/test_remote_auth.py drives the same gate over HTTP.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tool_call import call

from dialogflow_mcp import server_es

TESTS_DIR = Path(__file__).resolve().parent
ADMIN_TOOLS = {"update_order_status", "get_recent_orders", "get_conversation_logs"}
# A tool belongs here only if every user-token holder may call it and see its output.
PUBLIC_TOOLS = {
    "detect_intent",
    "get_menu",
    "get_food_item_details",
    "track_order",
    "complete_order",
}
REFUSAL = {"error": server_es._ADMIN_REQUIRED_ERROR}


class _FakeCtx:
    def __init__(self, request: Any) -> None:
        self.request_context = type("RC", (), {"request": request})()


def _fake_request(role: Any, with_state: bool = True) -> Any:
    request = type("Req", (), {})()
    if with_state:
        request.state = type("State", (), {})()
        if role is not None:
            request.state.mcp_role = role
    return request


def _set_request(monkeypatch: pytest.MonkeyPatch, request: Any) -> None:
    """Make server_es.mcp.get_context() return a context holding `request`."""
    monkeypatch.setattr(server_es.mcp, "get_context", lambda: _FakeCtx(request))


def _set_no_context(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> None:
        raise ValueError("Context is not available outside of a request")

    monkeypatch.setattr(server_es.mcp, "get_context", _raise)


def _gate_flags(monkeypatch: pytest.MonkeyPatch, enabled: bool, remote_required: bool) -> None:
    monkeypatch.setattr(server_es, "ADMIN_TOOLS_ENABLED", enabled)
    monkeypatch.setattr(server_es, "_REMOTE_ROLE_REQUIRED", remote_required)


def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("database reached by a refused admin tool")

    for fn in ("fetch_one", "fetch_all", "execute", "transaction"):
        monkeypatch.setattr(server_es, fn, boom)


_ORDER_ROW = {
    "order_id": 7,
    "customer_name": "Test Customer",
    "customer_phone": "+27 00 000 0007",
    "total_price": Decimal("10.00"),
    "order_status": "in progress",
    "delivery_address": "7 Example Street, Testville",
    "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
}


def test_pytest_process_never_built_a_remote_app():
    # create_remote_app() switches the gate to fail-closed for the whole
    # process. Tests that build a full app run it in a subprocess.
    assert server_es._REMOTE_ROLE_REQUIRED is False


class TestAdminDenied:
    def test_no_context_flag_off_allowed(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=False)
        _set_no_context(monkeypatch)
        assert server_es._admin_denied() is None

    def test_no_request_object_flag_off_allowed(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=False)
        _set_request(monkeypatch, None)
        assert server_es._admin_denied() is None

    def test_no_context_flag_on_refused(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_no_context(monkeypatch)
        assert server_es._admin_denied() == REFUSAL

    def test_no_request_object_flag_on_refused(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, None)
        assert server_es._admin_denied() == REFUSAL

    @pytest.mark.parametrize("required", [False, True])
    def test_user_role_refused(self, monkeypatch, required):
        _gate_flags(monkeypatch, enabled=True, remote_required=required)
        _set_request(monkeypatch, _fake_request("user"))
        assert server_es._admin_denied() == REFUSAL

    @pytest.mark.parametrize("role", [None, "", "ADMIN", "root", "admin "])
    def test_missing_or_unknown_role_refused(self, monkeypatch, role):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, _fake_request(role))
        assert server_es._admin_denied() == REFUSAL

    def test_request_without_state_refused(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=False)
        _set_request(monkeypatch, _fake_request(None, with_state=False))
        assert server_es._admin_denied() == REFUSAL

    def test_non_string_role_refused(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, _fake_request(["admin"]))
        assert server_es._admin_denied() == REFUSAL

    def test_admin_role_allowed(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, _fake_request("admin"))
        assert server_es._admin_denied() is None

    def test_require_request_role_sets_flag(self, monkeypatch):
        monkeypatch.setattr(server_es, "_REMOTE_ROLE_REQUIRED", False)
        server_es.require_request_role()
        assert server_es._REMOTE_ROLE_REQUIRED is True


class TestAdminToolDecorator:
    @pytest.mark.parametrize(
        "name,args",
        [
            ("update_order_status", (1, "delivered")),
            ("get_recent_orders", ()),
            ("get_conversation_logs", ()),
        ],
    )
    def test_user_request_refused_without_touching_db(self, monkeypatch, name, args):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, _fake_request("user"))
        _no_db(monkeypatch)
        assert call(getattr(server_es, name), *args) == REFUSAL

    def test_admin_request_runs_the_tool(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, _fake_request("admin"))
        monkeypatch.setattr(server_es, "fetch_all", lambda *a, **k: [])
        assert call(server_es.get_recent_orders) == {"orders": [], "count": 0}

    def test_direct_call_refused_in_remote_mode(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_no_context(monkeypatch)
        _no_db(monkeypatch)
        assert call(server_es.update_order_status, 1, "delivered") == REFUSAL

    def test_direct_call_allowed_outside_remote_mode(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=False)
        _set_no_context(monkeypatch)
        # An invalid status is answered before any SQL: proof the gate let it through.
        assert "Invalid status" in call(server_es.update_order_status, 1, "bogus")["error"]

    def test_gate_lookup_is_late_bound(self, monkeypatch):
        monkeypatch.setattr(server_es, "_admin_denied", lambda: {"error": "patched"})
        assert call(server_es.get_recent_orders) == {"error": "patched"}

    def test_wrapper_preserves_metadata(self):
        fn = server_es.update_order_status
        assert fn.__name__ == "update_order_status"
        assert hasattr(fn, "__wrapped__")
        assert "Allowed statuses" in (fn.__doc__ or "")

    def test_registry_holds_exactly_the_admin_tools(self):
        assert {f.__name__ for f in server_es._ADMIN_TOOLS} == ADMIN_TOOLS

    @pytest.mark.parametrize("role,expected", [("user", "user"), (None, "none")])
    def test_denial_logs_one_line_without_token(self, monkeypatch, caplog, role, expected):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        if role is None:
            _set_no_context(monkeypatch)
        else:
            request = _fake_request(role)
            request.headers = {"authorization": "Bearer user-token-0123456789abcdef"}
            _set_request(monkeypatch, request)
        with caplog.at_level("WARNING", logger="dialogflow_mcp.server_es"):
            call(server_es.update_order_status, 1, "delivered")
        denials = [r for r in caplog.records if "admin_tool_denied" in r.getMessage()]
        assert len(denials) == 1
        assert denials[0].levelname == "WARNING"
        msg = denials[0].getMessage()
        assert msg == f"admin_tool_denied tool=update_order_status role={expected}"
        assert "Bearer" not in msg and "token-0123" not in msg

    def test_allowed_call_logs_no_denial(self, monkeypatch, caplog):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, _fake_request("admin"))
        monkeypatch.setattr(server_es, "fetch_all", lambda *a, **k: [])
        with caplog.at_level("WARNING"):
            call(server_es.get_recent_orders)
        assert not [r for r in caplog.records if "admin_tool_denied" in r.getMessage()]


class TestTrackOrderAllowlist:
    def _track(self, monkeypatch, row=None):
        monkeypatch.setattr(server_es, "fetch_one", lambda *a, **k: dict(row or _ORDER_ROW))
        monkeypatch.setattr(server_es, "fetch_all", lambda *a, **k: [])
        return call(server_es.track_order, 7)["order"]

    def test_non_admin_sees_exactly_the_allowlist(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, _fake_request("user"))
        order = self._track(monkeypatch)
        assert set(order) == set(server_es._PUBLIC_ORDER_FIELDS)

    def test_unknown_new_column_stays_hidden(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, _fake_request("user"))
        order = self._track(monkeypatch, {**_ORDER_ROW, "customer_email": "a@example.com"})
        assert "customer_email" not in order
        assert set(order) == set(server_es._PUBLIC_ORDER_FIELDS)

    def test_admin_sees_everything(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, _fake_request("admin"))
        order = self._track(monkeypatch, {**_ORDER_ROW, "customer_email": "a@example.com"})
        assert set(order) == {*_ORDER_ROW, "customer_email"}

    def test_admin_tools_disabled_hides_pii_even_without_request(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=False, remote_required=False)
        _set_no_context(monkeypatch)
        order = self._track(monkeypatch)
        for field in server_es._CUSTOMER_PII_FIELDS:
            assert field not in order

    def test_no_request_in_remote_mode_hides_pii(self, monkeypatch):
        # ADMIN_TOOLS_ENABLED must be True, or `not ADMIN_TOOLS_ENABLED` alone
        # would hide the fields and the test would prove nothing about the gate.
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_no_context(monkeypatch)
        order = self._track(monkeypatch)
        assert set(order) == set(server_es._PUBLIC_ORDER_FIELDS)
        assert order["order_id"] == 7

    def test_stdio_direct_call_returns_pii(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=False)
        _set_no_context(monkeypatch)
        order = self._track(monkeypatch)
        for field in server_es._CUSTOMER_PII_FIELDS:
            assert field in order

    def test_hiding_pii_logs_no_denial(self, monkeypatch, caplog):
        _gate_flags(monkeypatch, enabled=True, remote_required=True)
        _set_request(monkeypatch, _fake_request("user"))
        with caplog.at_level("WARNING"):
            self._track(monkeypatch)
        assert not [r for r in caplog.records if "admin_tool_denied" in r.getMessage()]


class TestDatabaseErrorsAreGeneric:
    """Every database tool turns a psycopg error into one generic message."""

    SECRETISH = "connection to server at db.internal.example (10.0.0.5), user secretuser failed"
    CALLS = [
        ("get_menu", ()),
        ("get_food_item_details", ("Kota",)),
        ("track_order", (1,)),
        ("complete_order", ("A", "1", [{"name": "Kota", "quantity": 1}])),
        ("update_order_status", (1, "delivered")),
        ("get_recent_orders", ()),
        ("get_conversation_logs", ()),
    ]

    @pytest.fixture(autouse=True)
    def _failing_db(self, monkeypatch):
        _gate_flags(monkeypatch, enabled=True, remote_required=False)
        _set_no_context(monkeypatch)

        def fail(*_a: Any, **_k: Any) -> None:
            raise psycopg.OperationalError(self.SECRETISH)

        for fn in ("fetch_one", "fetch_all", "execute", "transaction"):
            monkeypatch.setattr(server_es, fn, fail)

    def test_calls_cover_every_database_tool(self):
        assert {name for name, _ in self.CALLS} == (PUBLIC_TOOLS | ADMIN_TOOLS) - {"detect_intent"}

    @pytest.mark.parametrize("name,args", CALLS, ids=[c[0] for c in CALLS])
    def test_generic_error_and_one_clean_log_line(self, caplog, name, args):
        with caplog.at_level("WARNING", logger="dialogflow_mcp.server_es"):
            res = call(getattr(server_es, name), *args)
        assert res == {"error": server_es._DB_UNAVAILABLE_ERROR}
        lines = [r.getMessage() for r in caplog.records]
        assert lines == [f"database_error tool={name} error=OperationalError sqlstate=none"]
        assert "db.internal" not in caplog.text and "secretuser" not in caplog.text

    def test_pool_timeout_is_generic(self, monkeypatch):
        from psycopg_pool import PoolTimeout

        def timeout(*_a: Any, **_k: Any) -> None:
            raise PoolTimeout("couldn't get a connection after 2.00 sec")

        monkeypatch.setattr(server_es, "fetch_all", timeout)
        assert call(server_es.get_menu) == {"error": server_es._DB_UNAVAILABLE_ERROR}

    def test_detect_intent_log_failure_does_not_leak(self, monkeypatch, caplog):
        monkeypatch.setattr(server_es, "PROJECT", "demo-project")

        class FakeResult:
            intent = type("I", (), {"display_name": "new.order"})()
            intent_detection_confidence = 0.9
            fulfillment_text = "ok"
            output_contexts: list[Any] = []
            action = ""
            all_required_params_present = True
            _pb = None

        class FakeClient:
            def detect_intent(self, request: Any) -> Any:
                return type("R", (), {"query_result": FakeResult()})()

        monkeypatch.setattr(server_es, "_sessions_client", lambda: FakeClient())
        monkeypatch.setattr(server_es, "MessageToDict", lambda _pb: {})
        with caplog.at_level("WARNING", logger="dialogflow_mcp.server_es"):
            res = call(server_es.detect_intent, "new order", "s-1")
        assert res["intent"] == "new.order"
        assert "db.internal" not in caplog.text and "secretuser" not in caplog.text
        assert "conversation_log_failed session=s-1 error=OperationalError" in caplog.text


# ── Tool inventory (fresh interpreter: tools register at import) ──────────

_INVENTORY_SNIPPET = """
import asyncio, json
from dialogflow_mcp import server_es
from dialogflow_mcp.server_es import mcp

PUBLIC = json.loads({public!r})
tools = asyncio.run(mcp.list_tools())
names = sorted(t.name for t in tools)
schemas = {{t.name: t.inputSchema for t in tools}}
registry = [f.__name__ for f in server_es._ADMIN_TOOLS]

server_es.require_request_role()

class _Ctx:
    class request_context:
        class request:
            class state:
                mcp_role = "user"

server_es.mcp.get_context = lambda: _Ctx()

def _boom(*a, **k):
    raise RuntimeError("db touched")

server_es.fetch_one = server_es.fetch_all = server_es.execute = server_es.transaction = _boom

refusals = {{}}
for name in names:
    if name in PUBLIC:
        continue
    fn = mcp._tool_manager.get_tool(name).fn
    try:
        refusals[name] = asyncio.run(fn())
    except BaseException as exc:
        refusals[name] = type(exc).__name__
print(json.dumps({{"names": names, "schemas": schemas, "registry": registry,
                   "refusals": refusals}}))
"""


@pytest.fixture(scope="module")
def tool_inventory() -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MCP_")}
    env["MCP_ADMIN_TOOLS"] = "on"
    env["PYTHONIOENCODING"] = "utf-8"
    snippet = _INVENTORY_SNIPPET.format(public=json.dumps(sorted(PUBLIC_TOOLS)))
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    result: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return result


class TestToolInventory:
    MESSAGE = (
        "Admin tools must use @admin_tool. Add a name to PUBLIC_TOOLS only if "
        "every user-token holder may call it and see its output."
    )

    def test_every_tool_is_classified(self, tool_inventory):
        assert set(tool_inventory["names"]) == PUBLIC_TOOLS | ADMIN_TOOLS, self.MESSAGE

    def test_registry_is_exactly_the_admin_tools(self, tool_inventory):
        assert set(tool_inventory["registry"]) == ADMIN_TOOLS, self.MESSAGE

    def test_every_non_public_tool_refuses_a_user_request(self, tool_inventory):
        assert set(tool_inventory["refusals"]) == ADMIN_TOOLS, self.MESSAGE
        for name, result in tool_inventory["refusals"].items():
            assert result == REFUSAL, f"{name}: {result!r}. {self.MESSAGE}"

    def test_schemas_unchanged_by_decorator(self, tool_inventory):
        base = json.loads((TESTS_DIR / "tool_schemas_base.json").read_text(encoding="utf-8"))
        assert tool_inventory["schemas"] == base, (
            "tool input schemas differ from tests/tool_schemas_base.json; if a tool "
            "signature changed on purpose (or after an mcp/pydantic upgrade), "
            "regenerate the snapshot"
        )
