"""Unit tests: no database, no network, no GCP credentials.

Anything that depends on import-time environment (MCP_ADMIN_TOOLS) or on the
once-per-process FastMCP session manager runs in a fresh subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tool_call import call

from dialogflow_mcp import database, remote_server, server_es

TESTS_DIR = Path(__file__).resolve().parent
ADMIN_TOOLS = {"update_order_status", "get_recent_orders", "get_conversation_logs"}
PUBLIC_TOOLS = {
    "detect_intent",
    "get_menu",
    "get_food_item_details",
    "track_order",
    "complete_order",
}


def _clean_env(**overrides: str | None) -> dict[str, str]:
    env = dict(os.environ)
    for var in (
        "MCP_ADMIN_TOOLS",
        "MCP_ADMIN_TOKEN",
        "MCP_AUTH_TOKEN",
        "MCP_TRANSPORT",
        "MCP_ALLOWED_HOSTS",
        "MCP_ALLOWED_ORIGINS",
        "GCP_PROJECT",
    ):
        env.pop(var, None)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _run_json(args: list[str], env: dict[str, str]) -> dict | list:
    proc = subprocess.run(
        [sys.executable, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    last = [line for line in proc.stdout.splitlines() if line.strip()][-1]
    return json.loads(last)


# ═══════════════════════════════════════════════════════════
#  server_es helpers
# ═══════════════════════════════════════════════════════════


class TestSessionPath:
    def test_global(self):
        assert (
            server_es._session_path("s1", project="p", location="global", environment="")
            == "projects/p/agent/sessions/s1"
        )

    def test_empty_location_is_global(self):
        assert (
            server_es._session_path("s1", project="p", location="", environment="")
            == "projects/p/agent/sessions/s1"
        )

    def test_regional(self):
        assert (
            server_es._session_path("s1", project="p", location="europe-west2", environment="")
            == "projects/p/locations/europe-west2/agent/sessions/s1"
        )

    def test_global_with_environment(self):
        assert (
            server_es._session_path("s1", project="p", location="global", environment="prod")
            == "projects/p/agent/environments/prod/users/-/sessions/s1"
        )

    def test_regional_with_environment(self):
        assert (
            server_es._session_path("s1", project="p", location="us-central1", environment="prod")
            == "projects/p/locations/us-central1/agent/environments/prod/users/-/sessions/s1"
        )

    def test_missing_project_raises(self):
        with pytest.raises(ValueError, match="GCP_PROJECT"):
            server_es._session_path("s1", project="", location="global", environment="")

    def test_defaults_from_module_config(self, monkeypatch):
        monkeypatch.setattr(server_es, "PROJECT", "modproj")
        monkeypatch.setattr(server_es, "LOCATION", "asia-northeast1")
        monkeypatch.setattr(server_es, "ENVIRONMENT", "")
        assert (
            server_es._session_path("abc")
            == "projects/modproj/locations/asia-northeast1/agent/sessions/abc"
        )

    def test_detect_intent_without_project_fails_before_network(self, monkeypatch):
        monkeypatch.setattr(server_es, "PROJECT", "")

        def _boom():  # pragma: no cover - must not be reached
            raise AssertionError("SessionsClient must not be created without a project")

        monkeypatch.setattr(server_es, "_sessions_client", _boom)
        with pytest.raises(ValueError, match="GCP_PROJECT"):
            call(server_es.detect_intent, "hello")


class TestEscapeLike:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Bunny Chow", "Bunny Chow"),
            ("%", r"\%"),
            ("_", r"\_"),
            ("50%_off", r"50\%\_off"),
            ("back\\slash", "back\\\\slash"),
            ("\\%", "\\\\\\%"),  # backslash escaped first, then %
            ("", ""),
        ],
    )
    def test_escape(self, raw, expected):
        assert server_es._escape_like(raw) == expected


class TestParseQuantity:
    @pytest.mark.parametrize(
        "raw, expected",
        [(1, 1), (50, 50), ("3", 3), (" 7 ", 7), (2.0, 2)],
    )
    def test_valid(self, raw, expected):
        assert server_es._parse_quantity(raw) == (expected, None)

    @pytest.mark.parametrize(
        "raw",
        [
            0,
            -1,
            51,
            10**30,
            True,
            False,
            2.5,
            "2.5",
            "abc",
            "",
            None,
            [],
            {},
            float("nan"),
            float("inf"),
        ],
    )
    def test_invalid(self, raw):
        qty, err = server_es._parse_quantity(raw)
        assert qty is None
        assert "between 1 and 50" in err


class TestClampLimit:
    @pytest.mark.parametrize(
        "raw, default, expected",
        [
            (10, 10, 10),
            (0, 10, 1),
            (-5, 10, 1),
            (1000, 10, server_es.MAX_LIST_LIMIT),
            ("25", 10, 25),
            ("abc", 20, 20),
            (None, 20, 20),
            (True, 20, 20),
            (3.9, 10, 3),
            (float("inf"), 10, 10),
        ],
    )
    def test_clamp(self, raw, default, expected):
        assert server_es._clamp_limit(raw, default) == expected


class TestCompleteOrderValidation:
    """Inputs rejected before any DB access."""

    @pytest.fixture(autouse=True)
    def _no_db(self, monkeypatch):
        def _boom(*_a, **_k):  # pragma: no cover - must not be reached
            raise AssertionError("complete_order touched the database")

        monkeypatch.setattr(server_es, "transaction", _boom)

    @pytest.mark.parametrize("items", [[], None, "Bunny Chow", {"name": "Kota"}])
    def test_empty_or_not_list(self, items):
        res = call(server_es.complete_order, "A", "1", items)
        assert "at least one item" in res["error"]

    def test_non_dict_entry(self):
        res = call(server_es.complete_order, "A", "1", [{"name": "Kota"}, "Kota"])
        assert "must be an object" in res["error"]

    @pytest.mark.parametrize("name", [None, "", "   ", 123])
    def test_bad_name(self, name):
        res = call(server_es.complete_order, "A", "1", [{"name": name, "quantity": 1}])
        assert "non-empty 'name'" in res["error"]

    @pytest.mark.parametrize("qty", [0, 51, -2, 1.5, "x", True])
    def test_bad_quantity(self, qty):
        res = call(server_es.complete_order, "A", "1", [{"name": " Kota ", "quantity": qty}])
        assert res["error"].startswith("Item 'Kota':")
        assert "between 1 and 50" in res["error"]


class TestSerialise:
    def test_decimal_and_datetime(self):
        from datetime import datetime, timezone
        from decimal import Decimal

        ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        out = server_es._serialise([{"p": Decimal("85.00"), "t": ts, "n": {"x": Decimal("1.5")}}])
        assert out == [{"p": 85.0, "t": ts.isoformat(), "n": {"x": 1.5}}]
        assert server_es._serialise(None) is None


# ═══════════════════════════════════════════════════════════
#  Admin tool gating (server_es reads MCP_ADMIN_TOOLS at import)
# ═══════════════════════════════════════════════════════════

_LIST_ES_TOOLS = (
    "import asyncio, json;"
    "from dialogflow_mcp import server_es as s;"
    "print(json.dumps(sorted(t.name for t in asyncio.run(s.mcp.list_tools()))))"
)


@pytest.mark.parametrize(
    "value, expect_admin",
    [
        (None, True),  # stdio default: on
        ("on", True),
        ("TRUE", True),
        ("1", True),
        ("off", False),
        ("0", False),
        ("no", False),
        ("", False),
    ],
)
def test_es_admin_tool_gating(value, expect_admin):
    tools = set(_run_json(["-c", _LIST_ES_TOOLS], _clean_env(MCP_ADMIN_TOOLS=value)))
    assert tools >= PUBLIC_TOOLS
    if expect_admin:
        assert tools >= ADMIN_TOOLS
    else:
        assert not (ADMIN_TOOLS & tools)


def test_admin_functions_exist_even_when_unregistered():
    # Module-level functions are always importable (used by the DB tests).
    for name in ADMIN_TOOLS:
        assert callable(getattr(server_es, name))


# ═══════════════════════════════════════════════════════════
#  CX server config validation
# ═══════════════════════════════════════════════════════════


class TestCxConfig:
    @pytest.fixture()
    def cx(self, monkeypatch):
        from dialogflow_mcp import server as cx_server

        monkeypatch.setattr(cx_server, "PROJECT", "")
        monkeypatch.setattr(cx_server, "AGENT_ID", "")
        monkeypatch.setattr(cx_server, "LOCATION", "global")
        for var in ("GCP_PROJECT", "DF_AGENT_ID", "DF_LOCATION"):
            monkeypatch.delenv(var, raising=False)
        return cx_server

    def test_both_missing(self, cx):
        with pytest.raises(ValueError) as exc:
            cx._require_config()
        assert "GCP_PROJECT" in str(exc.value)
        assert "DF_AGENT_ID" in str(exc.value)

    def test_agent_missing(self, cx, monkeypatch):
        monkeypatch.setenv("GCP_PROJECT", "p")
        with pytest.raises(ValueError) as exc:
            cx._require_config()
        assert "missing environment variable(s) DF_AGENT_ID." in str(exc.value)

    def test_project_missing(self, cx, monkeypatch):
        monkeypatch.setenv("DF_AGENT_ID", "agent")
        with pytest.raises(ValueError, match=r"missing environment variable\(s\) GCP_PROJECT\."):
            cx._require_config()

    def test_whitespace_counts_as_missing(self, cx, monkeypatch):
        monkeypatch.setenv("GCP_PROJECT", "   ")
        monkeypatch.setenv("DF_AGENT_ID", "agent")
        with pytest.raises(ValueError, match="GCP_PROJECT"):
            cx._require_config()

    def test_ok_and_location_default(self, cx, monkeypatch):
        monkeypatch.setenv("GCP_PROJECT", " p ")
        monkeypatch.setenv("DF_AGENT_ID", "agent")
        monkeypatch.setenv("DF_LOCATION", "")
        assert cx._require_config() == ("p", "global", "agent")

    def test_detect_intent_fails_before_network(self, cx, monkeypatch):
        def _boom(_loc):  # pragma: no cover - must not be reached
            raise AssertionError("client created without config")

        monkeypatch.setattr(cx, "_sessions_client", _boom)
        with pytest.raises(ValueError, match="DF_AGENT_ID"):
            call(cx.detect_intent, "hi")


# ═══════════════════════════════════════════════════════════
#  Remote server: config (in-process, nothing app-building)
# ═══════════════════════════════════════════════════════════


class TestRemoteConfig:
    def test_no_token_refused(self, monkeypatch):
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
        with pytest.raises(remote_server.ConfigError, match="MCP_AUTH_TOKEN is not set"):
            remote_server.create_remote_app()

    def test_blank_token_refused(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "    ")
        with pytest.raises(remote_server.ConfigError, match="not set"):
            remote_server.create_remote_app()

    def test_short_token_refused(self):
        with pytest.raises(remote_server.ConfigError, match="too short"):
            remote_server.create_remote_app(token="x" * 15)

    def test_min_length_token_accepted(self):
        assert remote_server._resolve_token("x" * 16) == "x" * 16

    def test_invalid_transport_refused(self):
        with pytest.raises(remote_server.ConfigError, match="MCP_TRANSPORT"):
            remote_server.create_remote_app(token="x" * 32, transport="websocket")

    def test_transport_default_and_normalised(self, monkeypatch):
        monkeypatch.delenv("MCP_TRANSPORT", raising=False)
        assert remote_server._resolve_transport() == "streamable-http"
        assert remote_server._resolve_transport(" SSE ") == "sse"

    def test_main_exits_without_token(self, monkeypatch):
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
        with pytest.raises(SystemExit, match="MCP_AUTH_TOKEN"):
            remote_server.main()

    def test_allowed_hosts_from_env(self, monkeypatch):
        monkeypatch.setenv(
            "MCP_ALLOWED_HOSTS", "mcp.example.com, api.example.com:443 ,[2001:db8::1]"
        )
        hosts = remote_server.build_allowed_hosts()
        assert "localhost" in hosts and "127.0.0.1:*" in hosts
        assert "mcp.example.com" in hosts and "mcp.example.com:*" in hosts
        assert "api.example.com:443" in hosts and "api.example.com:443:*" not in hosts
        assert "[2001:db8::1]" in hosts and "[2001:db8::1]:*" in hosts

    def test_allowed_origins_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://app.example.com")
        origins = remote_server.build_allowed_origins()
        assert "https://app.example.com" in origins
        assert "http://localhost:*" in origins

    def test_admin_tools_enabled_parsing(self, monkeypatch):
        monkeypatch.setenv("MCP_ADMIN_TOOLS", "Yes")
        assert remote_server.admin_tools_enabled()
        monkeypatch.setenv("MCP_ADMIN_TOOLS", "off")
        assert not remote_server.admin_tools_enabled()


# ═══════════════════════════════════════════════════════════
#  Remote server: real ASGI app via TestClient, one subprocess each
# ═══════════════════════════════════════════════════════════

PROBE = str(TESTS_DIR / "remote_probe.py")


@pytest.fixture(scope="module")
def streamable_probe() -> dict:
    return _run_json([PROBE, "streamable-http"], _clean_env())


@pytest.fixture(scope="module")
def sse_probe() -> dict:
    return _run_json([PROBE, "sse"], _clean_env())


@pytest.fixture(scope="module")
def streamable_admin_probe() -> dict:
    return _run_json([PROBE, "streamable-http"], _clean_env(MCP_ADMIN_TOOLS="on"))


class TestRemoteStreamableHttp:
    def test_default_transport(self, streamable_probe):
        assert streamable_probe["transport"] == "streamable-http"

    def test_health_public(self, streamable_probe):
        assert streamable_probe["health_status"] == 200
        assert streamable_probe["health_body"]["status"] == "healthy"

    def test_401_without_token(self, streamable_probe):
        assert streamable_probe["noauth_status"] == 401
        assert streamable_probe["noauth_www_authenticate"] == "Bearer"

    def test_401_wrong_token(self, streamable_probe):
        assert streamable_probe["wrongtoken_status"] == 401

    def test_401_token_without_bearer_prefix(self, streamable_probe):
        assert streamable_probe["nobearer_status"] == 401

    def test_421_disallowed_host(self, streamable_probe):
        assert streamable_probe["badhost_status"] == 421

    def test_initialize_and_list_tools(self, streamable_probe):
        assert streamable_probe["init_status"] == 200
        assert streamable_probe["init_server_name"] == "dialogflow-food-ordering-es"
        assert streamable_probe["has_session_id"]
        assert streamable_probe["initialized_status"] == 202
        assert streamable_probe["tools_status"] == 200
        assert set(streamable_probe["tools"]) == PUBLIC_TOOLS

    def test_admin_tools_absent_by_default(self, streamable_probe):
        assert not (ADMIN_TOOLS & set(streamable_probe["tools"]))

    def test_admin_tools_opt_in(self, streamable_admin_probe):
        assert streamable_admin_probe["tools_status"] == 200
        assert set(streamable_admin_probe["tools"]) >= ADMIN_TOOLS


class TestRemoteSse:
    def test_routes(self, sse_probe):
        assert sse_probe["transport"] == "sse"
        assert {"/sse", "/messages", "/health"} <= set(sse_probe["paths"])

    def test_health_public(self, sse_probe):
        assert sse_probe["health_status"] == 200
        assert sse_probe["health_body"]["transport"] == "sse"

    def test_auth_required(self, sse_probe):
        assert sse_probe["sse_noauth_status"] == 401
        assert sse_probe["messages_noauth_status"] == 401


# ═══════════════════════════════════════════════════════════
#  Database operations (mocking psycopg & psycopg_pool)
# ═══════════════════════════════════════════════════════════


class TestDatabaseOperations:
    """Unit tests for dialogflow_mcp.database with mocked psycopg_pool."""

    @pytest.fixture()
    def mock_pool_env(self, monkeypatch):
        """Provide a fake ConnectionPool, connection, transaction, and cursor."""
        database.close_pool()
        monkeypatch.setattr(database, "_pool", None)

        mock_cur = MagicMock()
        mock_cur.__enter__.return_value = mock_cur
        mock_cur.__exit__.return_value = False

        mock_tx = MagicMock()
        mock_tx.__enter__.return_value = mock_tx
        mock_tx.__exit__.return_value = False

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.cursor.return_value = mock_cur
        mock_conn.transaction.return_value = mock_tx

        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn

        pool_cls = MagicMock(return_value=mock_pool)
        monkeypatch.setattr(database, "ConnectionPool", pool_cls)

        yield {
            "pool_cls": pool_cls,
            "pool": mock_pool,
            "conn": mock_conn,
            "tx": mock_tx,
            "cur": mock_cur,
        }
        monkeypatch.setattr(database, "_pool", None)

    def test_database_url_default_and_override(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert database._database_url() == database.LOCAL_DEV_DATABASE_URL
        monkeypatch.setenv("DATABASE_URL", "   ")
        assert database._database_url() == database.LOCAL_DEV_DATABASE_URL
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/custom")
        assert database._database_url() == "postgresql://u:p@db:5432/custom"

    @pytest.mark.parametrize(
        "raw, expected",
        [(None, 5), ("10", 10), ("0", 1), ("-4", 1), ("not-an-int", 5)],
    )
    def test_pool_max_size(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("DB_POOL_MAX", raising=False)
        else:
            monkeypatch.setenv("DB_POOL_MAX", raw)
        assert database._pool_max_size() == expected

    @pytest.mark.parametrize(
        "raw, expected",
        [(None, True), ("on", True), ("1", True), ("off", False), ("0", False), ("no", False)],
    )
    def test_pool_check_toggle(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("DB_POOL_CHECK", raising=False)
        else:
            monkeypatch.setenv("DB_POOL_CHECK", raw)
        assert database._pool_check_enabled() is expected

    def test_pool_check_off_passes_none(self, mock_pool_env, monkeypatch):
        monkeypatch.setenv("DB_POOL_CHECK", "off")
        database.get_pool()
        _, kwargs = mock_pool_env["pool_cls"].call_args
        assert kwargs["check"] is None
        database.close_pool()

    def test_get_pool_lazy_singleton_and_close(self, mock_pool_env, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://test/db")
        p1 = database.get_pool()
        p2 = database.get_pool()
        assert p1 is p2 is mock_pool_env["pool"]
        mock_pool_env["pool_cls"].assert_called_once()
        _, kwargs = mock_pool_env["pool_cls"].call_args
        assert kwargs["open"] is False
        assert kwargs["kwargs"] == {"row_factory": database.dict_row}
        assert kwargs["check"] is database.ConnectionPool.check_connection
        mock_pool_env["pool"].open.assert_called_once()

        database.close_pool()
        mock_pool_env["pool"].close.assert_called_once()
        assert database._pool is None

        # Calling close_pool again when already None is a no-op
        database.close_pool()
        assert mock_pool_env["pool"].close.call_count == 1

    def test_fetch_all(self, mock_pool_env):
        expected = [{"id": 1, "name": "Bunny Chow"}, {"id": 2, "name": "Kota"}]
        mock_pool_env["cur"].fetchall.return_value = expected

        rows = database.fetch_all("SELECT * FROM food_items WHERE is_available = %s", (True,))
        assert rows == expected
        mock_pool_env["cur"].execute.assert_called_once_with(
            "SELECT * FROM food_items WHERE is_available = %s", (True,)
        )

    def test_fetch_one(self, mock_pool_env):
        expected = {"order_id": 1001, "order_status": "delivered"}
        mock_pool_env["cur"].fetchone.return_value = expected

        row = database.fetch_one("SELECT * FROM orders WHERE order_id = %s", (1001,))
        assert row == expected
        mock_pool_env["cur"].execute.assert_called_once_with(
            "SELECT * FROM orders WHERE order_id = %s", (1001,)
        )

    def test_execute_returns_rowcount(self, mock_pool_env):
        mock_pool_env["cur"].rowcount = 3
        count = database.execute("UPDATE orders SET order_status = %s", ("delivered",))
        assert count == 3
        mock_pool_env["cur"].execute.assert_called_once_with(
            "UPDATE orders SET order_status = %s", ("delivered",)
        )

    def test_transaction_commits_and_shares_single_cursor(self, mock_pool_env):
        with database.transaction() as cur:
            cur.execute("INSERT INTO orders VALUES (1)")
            cur.execute("INSERT INTO order_items VALUES (1, 2)")

        mock_pool_env["conn"].transaction.assert_called_once()
        assert mock_pool_env["cur"].execute.call_count == 2


# ═══════════════════════════════════════════════════════════
#  Intent resolution & parameter normalization in server_es.py
# ═══════════════════════════════════════════════════════════


class TestResolveFoodItemUnit:
    """Pure unit tests for _resolve_food_item using a fake query runner."""

    @pytest.mark.parametrize("bad_name", [None, "", "   ", 42, [], {}])
    def test_rejects_non_string_or_empty(self, bad_name):
        called = False

        def run_query(_sql, _params):  # pragma: no cover
            nonlocal called
            called = True
            return []

        row, err = server_es._resolve_food_item(run_query, bad_name)
        assert row is None
        assert "non-empty string" in err
        assert not called

    def test_exact_match_short_circuits_before_partial(self):
        calls = []
        bunny = {"id": 1, "name": "Bunny Chow", "price": Decimal("85.00"), "is_available": True}

        def run_query(sql, params):
            calls.append((sql, params))
            return [bunny]

        row, err = server_es._resolve_food_item(run_query, "  bunny chow  ")
        assert err is None
        assert row == bunny
        assert len(calls) == 1
        assert calls[0][1] == ("bunny chow",)

    def test_unambiguous_partial_match_escapes_wildcards(self):
        calls = []
        boerie = {
            "id": 2,
            "name": "Boerewors Roll",
            "price": Decimal("45.00"),
            "is_available": True,
        }

        def run_query(sql, params):
            calls.append((sql, params))
            if len(calls) == 1:
                return []  # no exact match
            return [boerie]

        row, err = server_es._resolve_food_item(run_query, "50%_boere")
        assert err is None
        assert row == boerie
        assert len(calls) == 2
        assert calls[1][1] == (r"%50\%\_boere%", server_es.MAX_MATCH_CANDIDATES + 1)

    def test_not_found_returns_clear_error(self):
        row, err = server_es._resolve_food_item(lambda _s, _p: [], "Sushi")
        assert row is None
        assert err == "Item 'Sushi' is not recognized on our menu."

    def test_ambiguous_partial_match_lists_candidates_and_truncates(self):
        candidates = [{"id": i, "name": f"Dish {i}"} for i in range(12)]

        def run_query(_sql, _params):
            if "lower(name) = lower" in _sql:
                return []
            return candidates

        row, err = server_es._resolve_food_item(run_query, "Dish")
        assert row is None
        assert "Item 'Dish' is ambiguous" in err
        assert "Dish 0" in err and "Dish 9" in err
        assert "Dish 10" not in err
        assert "(and more)" in err


class TestDetectIntentAndNormalization:
    """Unit tests for Dialogflow ES detect_intent and _sessions_client in server_es.py."""

    def test_sessions_client_global_vs_regional_caching(self, monkeypatch):
        fake_cls = MagicMock()
        monkeypatch.setattr(server_es.dialogflow, "SessionsClient", fake_cls)

        # Global endpoint
        monkeypatch.setattr(server_es, "_sessions_client_instance", None)
        monkeypatch.setattr(server_es, "LOCATION", "global")
        c1 = server_es._sessions_client()
        c2 = server_es._sessions_client()
        assert c1 is c2
        fake_cls.assert_called_once_with()

        # Regional endpoint
        fake_cls.reset_mock()
        monkeypatch.setattr(server_es, "_sessions_client_instance", None)
        monkeypatch.setattr(server_es, "LOCATION", "europe-west2")
        server_es._sessions_client()
        fake_cls.assert_called_once()
        opts = fake_cls.call_args.kwargs["client_options"]
        assert opts.api_endpoint == "europe-west2-dialogflow.googleapis.com"
        monkeypatch.setattr(server_es, "_sessions_client_instance", None)

    def test_detect_intent_normalizes_parameters_contexts_and_logs(self, monkeypatch):
        from google.cloud import dialogflow_v2 as df

        captured_req = {}
        captured_sql = {}

        class FakeSessionsClient:
            def detect_intent(self, request):
                captured_req["request"] = request
                return df.DetectIntentResponse(
                    query_result=df.QueryResult(
                        fulfillment_text="Got 2 Bunny Chows and 1 Kota. Anything else?",
                        action="order.add",
                        all_required_params_present=True,
                        intent=df.Intent(display_name="order.add"),
                        intent_detection_confidence=0.95,
                        parameters={
                            "food-item": ["Bunny Chow", "Kota"],
                            "number": [2, 1],
                        },
                        output_contexts=[
                            df.Context(
                                name="projects/p/agent/sessions/s-42/contexts/ongoing-order",
                                lifespan_count=5,
                                parameters={
                                    "food-item": ["Bunny Chow", "Kota"],
                                    "number": [2, 1],
                                },
                            )
                        ],
                    )
                )

        def fake_execute(sql, params):
            captured_sql["sql"] = sql
            captured_sql["params"] = params
            return 1

        monkeypatch.setattr(server_es, "PROJECT", "sa-food-bot")
        monkeypatch.setattr(server_es, "LOCATION", "global")
        monkeypatch.setattr(server_es, "ENVIRONMENT", "")
        monkeypatch.setattr(server_es, "_sessions_client", lambda: FakeSessionsClient())
        monkeypatch.setattr(server_es, "execute", fake_execute)

        result = call(
            server_es.detect_intent,
            "I want 2 bunny chows and 1 kota",
            session_id="s-42",
            language_code="en-ZA",
        )

        req = captured_req["request"]
        assert req.session == "projects/sa-food-bot/agent/sessions/s-42"
        assert req.query_input.text.text == "I want 2 bunny chows and 1 kota"
        assert req.query_input.text.language_code == "en-ZA"

        assert result == {
            "session_id": "s-42",
            "fulfillment_text": "Got 2 Bunny Chows and 1 Kota. Anything else?",
            "intent": "order.add",
            "confidence": pytest.approx(0.95),
            "parameters": {
                "food-item": ["Bunny Chow", "Kota"],
                "number": [2.0, 1.0],
            },
            "contexts": [
                {
                    "name": "ongoing-order",
                    "lifespan": 5,
                    "parameters": {
                        "food-item": ["Bunny Chow", "Kota"],
                        "number": [2.0, 1.0],
                    },
                }
            ],
            "action": "order.add",
            "all_required_params_present": True,
        }

        assert "INSERT INTO conversation_logs" in captured_sql["sql"]
        sid, msg, bot, intent, params_json, conf = captured_sql["params"]
        assert sid == "s-42"
        assert msg == "I want 2 bunny chows and 1 kota"
        assert bot == "Got 2 Bunny Chows and 1 Kota. Anything else?"
        assert intent == "order.add"
        assert json.loads(params_json) == {
            "food-item": ["Bunny Chow", "Kota"],
            "number": [2.0, 1.0],
        }
        assert conf == pytest.approx(0.95)

    def test_detect_intent_generates_session_id_and_tolerates_db_failure(self, monkeypatch):
        from google.cloud import dialogflow_v2 as df

        class FakeSessionsClient:
            def detect_intent(self, request):
                return df.DetectIntentResponse(
                    query_result=df.QueryResult(
                        fulfillment_text="Order #1001 is out for delivery.",
                        action="",
                        all_required_params_present=True,
                        intent=df.Intent(display_name="track.order"),
                        intent_detection_confidence=0.88,
                        parameters={"order_id": 1001},
                    )
                )

        def failing_execute(_sql, _params):
            raise RuntimeError("DB connection refused")

        monkeypatch.setattr(server_es, "PROJECT", "sa-food-bot")
        monkeypatch.setattr(server_es, "LOCATION", "global")
        monkeypatch.setattr(server_es, "ENVIRONMENT", "")
        monkeypatch.setattr(server_es, "_sessions_client", lambda: FakeSessionsClient())
        monkeypatch.setattr(server_es, "execute", failing_execute)

        result = call(server_es.detect_intent, "Where is order 1001?")
        assert len(result["session_id"]) == 36  # generated UUID4
        assert result["intent"] == "track.order"
        assert result["parameters"] == {"order_id": 1001.0}
        assert result["action"] is None
        assert result["contexts"] == []


class TestServerEsDatabaseToolsMocked:
    """Unit tests for server_es DB tools with mocked database helpers (no live Postgres)."""

    def test_get_menu_all_and_filtered(self, monkeypatch):
        calls = []

        def fake_fetch_all(sql, params=None):
            calls.append((sql, params))
            return [
                {
                    "id": 1,
                    "name": "Bunny Chow",
                    "description": "Quarter loaf curry",
                    "price": Decimal("85.00"),
                    "category": "Main",
                    "is_available": True,
                }
            ]

        monkeypatch.setattr(server_es, "fetch_all", fake_fetch_all)

        res_all = call(server_es.get_menu)
        assert res_all["total_items"] == 1
        assert res_all["menu"][0]["price"] == 85.0
        assert isinstance(res_all["menu"][0]["price"], float)
        assert calls[-1][1] is None

        res_cat = call(server_es.get_menu, "  Street_Food% ")
        assert res_cat["total_items"] == 1
        assert calls[-1][1] == (r"%Street\_Food\%%",)

    def test_get_food_item_details_success_and_error(self, monkeypatch):
        monkeypatch.setattr(
            server_es,
            "fetch_all",
            lambda _sql, _params: [
                {"id": 6, "name": "Kota", "price": Decimal("40.00"), "is_available": True}
            ],
        )
        res = call(server_es.get_food_item_details, "Kota")
        assert res == {"item": {"id": 6, "name": "Kota", "price": 40.0, "is_available": True}}

        monkeypatch.setattr(server_es, "fetch_all", lambda _sql, _params: [])
        err_res = call(server_es.get_food_item_details, "Unknown")
        assert "not recognized" in err_res["error"]

    def test_track_order_found_and_not_found(self, monkeypatch):
        monkeypatch.setattr(server_es, "fetch_one", lambda _s, _p: None)
        assert "not found" in call(server_es.track_order, 9999)["error"]

        ts = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(
            server_es,
            "fetch_one",
            lambda _s, _p: {
                "order_id": 1001,
                "customer_name": "Thabo",
                "total_price": Decimal("115.00"),
                "order_status": "delivered",
                "created_at": ts,
            },
        )
        monkeypatch.setattr(
            server_es,
            "fetch_all",
            lambda _s, _p: [
                {
                    "item_name": "Bunny Chow",
                    "quantity": 1,
                    "unit_price": Decimal("85.00"),
                    "total_price": Decimal("85.00"),
                }
            ],
        )
        res = call(server_es.track_order, 1001)
        assert res["order"]["total_price"] == 115.0
        assert res["order"]["created_at"] == ts.isoformat()
        assert res["items"][0]["unit_price"] == 85.0

    def test_complete_order_mocked_transaction_merges_duplicates(self, monkeypatch):
        from contextlib import contextmanager

        executed = []

        class FakeCursor:
            def execute(self, sql, params=None):
                executed.append((sql, params))

            def fetchall(self):
                sql, _ = executed[-1]
                if "FROM food_items" in sql:
                    return [
                        {
                            "id": 6,
                            "name": "Kota",
                            "description": "Quarter loaf",
                            "price": Decimal("40.00"),
                            "category": "Street Food",
                            "is_available": True,
                        }
                    ]
                return []

            def fetchone(self):
                return {
                    "order_id": 2001,
                    "total_price": Decimal("120.00"),
                    "order_status": "in progress",
                    "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
                }

        @contextmanager
        def fake_transaction():
            yield FakeCursor()

        monkeypatch.setattr(server_es, "transaction", fake_transaction)
        res = call(
            server_es.complete_order,
            "Naledi",
            "+27000000001",
            [{"name": "Kota", "quantity": 1}, {"name": "kota", "quantity": "2"}],
        )
        assert res["success"] is True
        assert res["order_id"] == 2001
        assert res["total_price_zar"] == 120.0
        assert res["items"] == [
            {"name": "Kota", "quantity": 3, "unit_price": 40.0, "total_price": 120.0}
        ]

    def test_complete_order_mocked_out_of_stock_and_over_limit(self, monkeypatch):
        from contextlib import contextmanager

        available = True

        class FakeCursor:
            def execute(self, sql, params=None):
                pass

            def fetchall(self):
                return [
                    {
                        "id": 6,
                        "name": "Kota",
                        "description": "Quarter loaf",
                        "price": Decimal("40.00"),
                        "category": "Street Food",
                        "is_available": available,
                    }
                ]

        @contextmanager
        def fake_transaction():
            yield FakeCursor()

        monkeypatch.setattr(server_es, "transaction", fake_transaction)

        # Merged quantity > 50 triggers _OrderAborted
        over = call(
            server_es.complete_order,
            "A",
            "1",
            [{"name": "Kota", "quantity": 30}, {"name": "Kota", "quantity": 25}],
        )
        assert "exceeds the maximum of 50" in over["error"]

        # Out-of-stock item triggers _OrderAborted
        available = False
        oos = call(server_es.complete_order, "A", "1", [{"name": "Kota", "quantity": 1}])
        assert "currently out of stock" in oos["error"]

    def test_admin_tools_mocked(self, monkeypatch):
        # update_order_status
        assert "Invalid status" in call(server_es.update_order_status, 1001, "unknown")["error"]
        monkeypatch.setattr(server_es, "execute", lambda _s, _p: 0)
        assert "not found" in call(server_es.update_order_status, 1001, "preparing")["error"]
        monkeypatch.setattr(server_es, "execute", lambda _s, _p: 1)
        ok = call(server_es.update_order_status, 1001, "  Preparing ")
        assert ok["success"] is True and ok["new_status"] == "preparing"

        # get_recent_orders & get_conversation_logs
        recorded_params = []

        def fake_fetch_all(_sql, params=None):
            recorded_params.append(params)
            return [{"order_id": 1001, "total_price": Decimal("50.00")}]

        monkeypatch.setattr(server_es, "fetch_all", fake_fetch_all)
        orders_res = call(server_es.get_recent_orders, limit=500, status="Delivered")
        assert orders_res["count"] == 1
        assert orders_res["orders"][0]["total_price"] == 50.0
        assert recorded_params[-1] == ("delivered", 100)

        logs_res = call(server_es.get_conversation_logs, session_id="sess-99", limit=-5)
        assert logs_res["count"] == 1
        assert recorded_params[-1] == ("sess-99", 1)


# ═══════════════════════════════════════════════════════════
#  Remote authentication & token validation in remote_server.py
# ═══════════════════════════════════════════════════════════


class TestBearerAuthASGIMiddleware:
    """In-process unit tests for BearerAuthASGIMiddleware and token validation."""

    TOKEN = "valid-secret-token-0123456789"

    @staticmethod
    def _invoke(
        token: str,
        path: str = "/mcp",
        headers: dict[str, str] | None = None,
        scope_type: str = "http",
    ) -> dict:
        import asyncio

        events: list[dict] = []
        inner_called = False

        async def inner_app(scope, receive, send):
            nonlocal inner_called
            inner_called = True
            if scope["type"] == "http":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send({"type": "http.response.body", "body": b"secret-data"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(event):
            events.append(event)

        raw_headers = [
            (k.encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
        ]
        scope = {"type": scope_type, "path": path, "headers": raw_headers}
        mw = remote_server.BearerAuthASGIMiddleware(inner_app, token=token)
        asyncio.run(mw(scope, receive, send))

        start = next((e for e in events if e["type"] == "http.response.start"), None)
        body_bytes = b"".join(
            e.get("body", b"") for e in events if e["type"] == "http.response.body"
        )
        resp_headers = (
            {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start["headers"]}
            if start
            else {}
        )
        return {
            "inner_called": inner_called,
            "status": start["status"] if start else None,
            "headers": resp_headers,
            "body": body_bytes.decode("utf-8") if body_bytes else "",
        }

    def test_health_endpoint_bypasses_auth(self):
        res = self._invoke(self.TOKEN, path="/health")
        assert res["status"] == 200
        assert res["inner_called"] is True

    def test_valid_bearer_token_allows_request(self):
        res = self._invoke(
            self.TOKEN,
            path="/mcp",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        assert res["status"] == 200
        assert res["inner_called"] is True
        assert res["body"] == "secret-data"

    def test_case_insensitive_authorization_header_name(self):
        res = self._invoke(
            self.TOKEN,
            path="/mcp",
            headers={"AUTHoRiZaTiOn": f"Bearer {self.TOKEN}"},
        )
        assert res["status"] == 200
        assert res["inner_called"] is True

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": ""},
            {"Authorization": "Bearer wrong-token-000000000000"},
            {"Authorization": TOKEN},  # missing "Bearer " prefix
            {"Authorization": f"bearer {TOKEN}"},  # lowercase scheme
            {"Authorization": f"Basic {TOKEN}"},
            {"Authorization": f"Bearer {TOKEN} "},  # trailing space
        ],
    )
    def test_missing_or_invalid_token_returns_401(self, headers: dict):
        res = self._invoke(self.TOKEN, path="/mcp", headers=headers)
        assert res["status"] == 401
        assert res["inner_called"] is False
        assert res["headers"].get("www-authenticate") == "Bearer"
        body = json.loads(res["body"])
        assert body["error"] == "Unauthorized"
        assert "Bearer token" in body["message"]

    def test_non_http_scope_passes_through(self):
        res = self._invoke(self.TOKEN, scope_type="lifespan")
        assert res["inner_called"] is True
        assert res["status"] is None


class TestBearerAuthRolesAndScopes:
    """Role tagging, scope types and a constant number of token comparisons."""

    USER = "user-secret-token-0123456789"
    ADMIN = "admin-secret-token-0123456789"

    @staticmethod
    def _run_scope(scope, mw_kwargs, token="user-secret-token-0123456789"):
        import asyncio

        seen: list[dict] = []
        sent: list[dict] = []

        async def inner_app(scope, receive, send):
            seen.append(scope)
            if scope["type"] == "http":
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(event):
            sent.append(event)

        mw = remote_server.BearerAuthASGIMiddleware(inner_app, token=token, **mw_kwargs)
        asyncio.run(mw(scope, receive, send))
        return seen, sent

    def _role(self, header, **mw_kwargs):
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"authorization", header.encode("latin-1"))],
            "state": {"keep": 1},
        }
        seen, sent = self._run_scope(scope, mw_kwargs)
        return scope, seen, sent

    def test_admin_token_gets_admin_role(self):
        _, seen, _ = self._role(f"Bearer {self.ADMIN}", admin_token=self.ADMIN)
        assert seen[0]["state"]["mcp_role"] == "admin"

    def test_user_token_gets_user_role_when_admin_token_configured(self):
        _, seen, _ = self._role(f"Bearer {self.USER}", admin_token=self.ADMIN)
        assert seen[0]["state"]["mcp_role"] == "user"

    def test_user_role_admin_without_admin_token_gives_admin(self):
        _, seen, _ = self._role(f"Bearer {self.USER}", user_role="admin")
        assert seen[0]["state"]["mcp_role"] == "admin"

    def test_default_user_role_is_user(self):
        _, seen, _ = self._role(f"Bearer {self.USER}")
        assert seen[0]["state"]["mcp_role"] == "user"

    def test_original_scope_state_not_mutated(self):
        scope, seen, _ = self._role(f"Bearer {self.ADMIN}", admin_token=self.ADMIN)
        assert scope["state"] == {"keep": 1}
        assert seen[0]["state"] == {"keep": 1, "mcp_role": "admin"}
        assert seen[0]["state"] is not scope["state"]

    def test_admin_token_is_just_a_wrong_token_when_none_configured(self):
        _, seen, sent = self._role(f"Bearer {self.ADMIN}")
        assert seen == []
        assert sent[0]["status"] == 401

    @pytest.mark.parametrize(
        "header", ["", "Bearer nope", "Bearer user-secret-token-0123456789", "x" * 500]
    )
    def test_compare_digest_call_count_is_constant(self, monkeypatch, header):
        calls = []
        real = remote_server.hmac.compare_digest

        def counting(a, b):
            calls.append(1)
            return real(a, b)

        monkeypatch.setattr(remote_server.hmac, "compare_digest", counting)
        self._role(header, admin_token=self.ADMIN)
        assert len(calls) == 2
        calls.clear()
        self._role(header)
        assert len(calls) == 1

    def test_401_response_contains_neither_token(self):
        _, seen, sent = self._role("Bearer wrong", admin_token=self.ADMIN)
        assert seen == []
        assert sent[0]["status"] == 401
        blob = repr(sent)
        assert self.USER not in blob
        assert self.ADMIN not in blob

    def test_websocket_scope_closed_1008_and_app_not_called(self):
        seen, sent = self._run_scope(
            {"type": "websocket", "path": "/mcp", "headers": []}, {"admin_token": self.ADMIN}
        )
        assert seen == []
        assert sent == [{"type": "websocket.close", "code": 1008}]

    def test_lifespan_scope_reaches_app(self):
        seen, sent = self._run_scope({"type": "lifespan"}, {"admin_token": self.ADMIN})
        assert [s["type"] for s in seen] == ["lifespan"]
        assert sent == []

    def test_unknown_scope_type_does_not_reach_app(self):
        seen, sent = self._run_scope({"type": "weird", "path": "/mcp"}, {})
        assert seen == []
        assert sent == []


class TestSharedTokenAdminWarning:
    @staticmethod
    def _app(admin_configured):
        from types import SimpleNamespace

        return SimpleNamespace(state=SimpleNamespace(admin_token_configured=admin_configured))

    @pytest.mark.parametrize(
        "admin_configured,tools,expect_warning",
        [(False, "on", True), (False, "off", False), (True, "on", False), (True, "off", False)],
    )
    def test_warning_matrix(self, monkeypatch, admin_configured, tools, expect_warning):
        monkeypatch.setenv("MCP_ADMIN_TOOLS", tools)
        result = remote_server._shared_token_admin_warning(self._app(admin_configured))
        if expect_warning:
            assert result == remote_server.SHARED_TOKEN_ADMIN_WARNING
            assert "Every MCP_AUTH_TOKEN holder has admin rights" in result
        else:
            assert result is None
