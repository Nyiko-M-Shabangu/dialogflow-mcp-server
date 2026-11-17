"""Remote mode: startup configuration, the admin and user roles over HTTP
(admin tools and track_order's customer fields), Host/Origin pre-validation
and the one-line denial log.

Anything that builds the real app runs in a fresh subprocess (see
tests/remote_auth_probe.py and tests/remote_probe.py for why).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tool_call import call

from dialogflow_mcp import remote_server

TESTS_DIR = Path(__file__).resolve().parent
AUTH_PROBE = str(TESTS_DIR / "remote_auth_probe.py")
BASIC_PROBE = str(TESTS_DIR / "remote_probe.py")

USER_TOKEN = "user-token-0123456789abcdef"
ADMIN_TOKEN = "admin-token-0123456789abcdef"
ADMIN_TOOLS = {"update_order_status", "get_recent_orders", "get_conversation_logs"}
REFUSAL = "admin tool"
PAST_GATE = "Invalid status"
PII_FIELDS = {"customer_name", "customer_phone", "delivery_address"}
PUBLIC_ORDER_FIELDS = {"order_id", "total_price", "order_status", "created_at", "updated_at"}
DUMMY_DATABASE_URL = "postgresql://probe:probe@127.0.0.1:9/probe_unused"


def _env(**overrides: str | None) -> dict[str, str]:
    env = dict(os.environ)
    for var in (
        "MCP_ADMIN_TOOLS",
        "MCP_AUTH_TOKEN",
        "MCP_ADMIN_TOKEN",
        "MCP_TRANSPORT",
        "MCP_ALLOWED_HOSTS",
        "MCP_ALLOWED_ORIGINS",
        "GCP_PROJECT",
    ):
        env.pop(var, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["DATABASE_URL"] = DUMMY_DATABASE_URL
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _run_json(args: list[str], env: dict[str, str]) -> tuple[dict, str]:
    proc = _run(args, env)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    last = [line for line in proc.stdout.splitlines() if line.strip()][-1]
    return json.loads(last), proc.stderr


# ═══════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════


class TestAdminTokenConfig:
    def test_too_short(self):
        with pytest.raises(remote_server.ConfigError, match="MCP_ADMIN_TOKEN is too short"):
            remote_server.create_remote_app(token=USER_TOKEN, admin_token="x" * 15)

    def test_equal_to_auth_token(self):
        with pytest.raises(remote_server.ConfigError, match="must differ"):
            remote_server.create_remote_app(token=USER_TOKEN, admin_token=USER_TOKEN)

    def test_too_short_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_ADMIN_TOKEN", "short")
        with pytest.raises(remote_server.ConfigError, match="MCP_ADMIN_TOKEN is too short"):
            remote_server.create_remote_app(token=USER_TOKEN)

    def test_blank_means_unset(self):
        assert remote_server._resolve_admin_token("   ", USER_TOKEN) is None

    def test_explicit_admin_tools_off_contradicts_admin_token(self):
        code = (
            "from dialogflow_mcp import remote_server as r\n"
            "try:\n"
            "    r.create_remote_app()\n"
            "except r.ConfigError as e:\n"
            "    print('CONFIG_ERROR', e)\n"
        )
        proc = _run(
            ["-c", code],
            _env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOKEN=ADMIN_TOKEN, MCP_ADMIN_TOOLS="off"),
        )
        assert proc.returncode == 0, proc.stderr
        assert "CONFIG_ERROR" in proc.stdout and "MCP_ADMIN_TOOLS" in proc.stdout


# ═══════════════════════════════════════════════════════════
#  Both tokens configured: roles enforced inside the tools
# ═══════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def streamable_roles() -> tuple[dict, str]:
    return _run_json(
        [AUTH_PROBE, "streamable-http"],
        _env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOKEN=ADMIN_TOKEN),
    )


@pytest.fixture(scope="module")
def sse_roles() -> tuple[dict, str]:
    return _run_json(
        [AUTH_PROBE, "sse"],
        _env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOKEN=ADMIN_TOKEN),
    )


class TestStreamableRoles:
    def test_admin_token_registers_admin_tools(self, streamable_roles):
        out, _ = streamable_roles
        assert out["admin_token_configured"]
        # Listed for everyone (tools/list is not filtered per role) ...
        assert set(out["user"]["tools"]) >= ADMIN_TOOLS

    def test_user_token_initializes_and_is_refused(self, streamable_roles):
        user = streamable_roles[0]["user"]
        assert user["init_status"] == 200
        assert user["call_status"] == 200
        text = user["admin_call_text"]
        assert REFUSAL in json.loads(text)["error"]
        assert PAST_GATE not in text

    def test_admin_token_passes_gate(self, streamable_roles):
        admin = streamable_roles[0]["admin"]
        assert admin["init_status"] == 200
        assert PAST_GATE in json.loads(admin["admin_call_text"])["error"]

    def test_wrong_token_401(self, streamable_roles):
        assert streamable_roles[0]["wrongtoken_status"] == 401

    def test_bad_host_421_and_bad_origin_403(self, streamable_roles):
        out, _ = streamable_roles
        assert out["badhost_status"] == 421
        assert out["badhost_body"] == "Invalid Host header"
        # Host is checked before auth: a rebinding attempt never sees a 401.
        assert out["badhost_noauth_status"] == 421
        assert out["badorigin_status"] == 403
        assert out["badorigin_body"] == "Invalid Origin header"

    def test_health_exempt_from_host_check(self, streamable_roles):
        assert streamable_roles[0]["health_weird_host_status"] == 200

    def test_track_order_hides_customer_fields_from_user_token(self, streamable_roles):
        assert set(streamable_roles[0]["user"]["track_order_keys"]) == PUBLIC_ORDER_FIELDS

    def test_track_order_shows_customer_fields_to_admin_token(self, streamable_roles):
        keys = set(streamable_roles[0]["admin"]["track_order_keys"])
        assert keys == PUBLIC_ORDER_FIELDS | PII_FIELDS

    def test_no_traceback_logged(self, streamable_roles):
        assert "Traceback" not in streamable_roles[1]


class TestSseRoles:
    def test_user_token_refused_inside_tool(self, sse_roles):
        user = sse_roles[0]["user"]
        assert user["sse_status"] == 200 and user["init_ok"]
        assert REFUSAL in json.loads(user["admin_call_text"])["error"]

    def test_admin_token_passes_gate(self, sse_roles):
        admin = sse_roles[0]["admin"]
        assert admin["init_ok"]
        assert PAST_GATE in json.loads(admin["admin_call_text"])["error"]

    def test_wrong_token_401(self, sse_roles):
        assert sse_roles[0]["wrongtoken_status"] == 401

    def test_bad_host_421_without_traceback(self, sse_roles):
        out, stderr = sse_roles
        assert out["sse_badhost_status"] == 421
        assert out["sse_badhost_body"] == "Invalid Host header"
        assert out["messages_badhost_status"] == 421
        assert out["sse_badorigin_status"] == 403
        assert "Traceback" not in stderr
        assert "Request validation failed" not in stderr

    def test_health_exempt_from_host_check(self, sse_roles):
        assert sse_roles[0]["health_weird_host_status"] == 200

    def test_track_order_hides_customer_fields_from_user_token(self, sse_roles):
        assert set(sse_roles[0]["user"]["track_order_keys"]) == PUBLIC_ORDER_FIELDS

    def test_track_order_shows_customer_fields_to_admin_token(self, sse_roles):
        keys = set(sse_roles[0]["admin"]["track_order_keys"])
        assert keys == PUBLIC_ORDER_FIELDS | PII_FIELDS


# ═══════════════════════════════════════════════════════════
#  No MCP_ADMIN_TOKEN: one shared token
# ═══════════════════════════════════════════════════════════


class TestWithoutAdminToken:
    def test_admin_tools_absent_by_default(self):
        out, _ = _run_json([BASIC_PROBE, "streamable-http"], _env())
        assert out["tools_status"] == 200
        assert not (ADMIN_TOOLS & set(out["tools"]))
        assert out["badhost_status"] == 421
        assert out["noauth_status"] == 401

    def test_shared_token_is_admin_when_admin_tools_on(self):
        out, _ = _run_json(
            [AUTH_PROBE, "streamable-http"],
            _env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOOLS="on"),
        )
        assert not out["admin_token_configured"]
        assert set(out["user"]["tools"]) >= ADMIN_TOOLS
        assert PAST_GATE in json.loads(out["user"]["admin_call_text"])["error"]
        # The shared token is the admin role here, so it sees customer fields.
        assert set(out["user"]["track_order_keys"]) == PUBLIC_ORDER_FIELDS | PII_FIELDS

    def test_user_token_hides_customer_fields_when_admin_tools_off(self):
        out, _ = _run_json([AUTH_PROBE, "streamable-http"], _env(MCP_AUTH_TOKEN=USER_TOKEN))
        assert set(out["user"]["track_order_keys"]) == PUBLIC_ORDER_FIELDS


# ═══════════════════════════════════════════════════════════
#  Gate helper outside a request (stdio / direct calls)
# ═══════════════════════════════════════════════════════════


def test_admin_gate_allows_direct_calls():
    from dialogflow_mcp import server_es

    assert server_es._REMOTE_ROLE_REQUIRED is False, (
        "this test assumes the pytest process never built a remote app; "
        "build remote apps only in subprocesses"
    )
    assert server_es._admin_denied() is None
    # Direct call path used by the DB tests is not gated (no request context).
    assert PAST_GATE in call(server_es.update_order_status, 1, "bogus")["error"]


# ═══════════════════════════════════════════════════════════
#  After create_remote_app(): fail closed, one plain log line
# ═══════════════════════════════════════════════════════════


def test_direct_admin_call_after_remote_app_refused_with_one_log_line():
    snippet = (
        "from dialogflow_mcp import remote_server\n"
        "remote_server.create_remote_app()\n"
        "from dialogflow_mcp import server_es\n"
        "from tool_call import call\n"
        "print('RESULT', call(server_es.update_order_status, 1, 'x'))\n"
    )
    env = _env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOKEN=ADMIN_TOKEN)
    env["PYTHONPATH"] = str(TESTS_DIR)
    proc = _run(["-c", snippet], env)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert REFUSAL in proc.stdout
    lines = [
        ln
        for ln in proc.stderr.splitlines()
        if "admin_tool_denied tool=update_order_status role=none" in ln
    ]
    assert len(lines) == 1, proc.stderr
    assert " WARNING dialogflow_mcp.server_es admin_tool_denied" in lines[0]
    # The plain handler has no Rich `file:line` column.
    assert not [ln for ln in proc.stderr.splitlines() if "server_es.py:" in ln]
    for token in (USER_TOKEN, ADMIN_TOKEN):
        assert token not in proc.stdout
        assert token not in proc.stderr


# ═══════════════════════════════════════════════════════════
#  Startup configuration edge cases
# ═══════════════════════════════════════════════════════════


def _snippet_tools() -> str:
    return (
        "import asyncio\n"
        "from dialogflow_mcp import remote_server\n"
        "try:\n"
        "    app = remote_server.create_remote_app()\n"
        "except remote_server.ConfigError as exc:\n"
        "    print('CONFIG_ERROR', exc)\n"
        "    raise SystemExit(0)\n"
        "from dialogflow_mcp.server_es import mcp\n"
        "names = sorted(t.name for t in asyncio.run(mcp.list_tools()))\n"
        "print('TOOLS', ','.join(names))\n"
        "print('ADMIN_CONFIGURED', app.state.admin_token_configured)\n"
    )


def _tools_run(env: dict[str, str]) -> tuple[subprocess.CompletedProcess, set[str]]:
    proc = _run(["-c", _snippet_tools()], env)
    tools: set[str] = set()
    for line in proc.stdout.splitlines():
        if line.startswith("TOOLS "):
            tools = set(line[len("TOOLS ") :].split(","))
    return proc, tools


class TestDatabaseUrlRequired:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_database_url_is_config_error(self, value):
        proc, tools = _tools_run(_env(MCP_AUTH_TOKEN=USER_TOKEN, DATABASE_URL=value))
        assert "CONFIG_ERROR DATABASE_URL is not set" in proc.stdout, proc.stdout + proc.stderr
        assert not tools

    def test_main_exits_with_message(self):
        proc = _run(
            ["-c", "from dialogflow_mcp import remote_server; remote_server.main()"],
            _env(MCP_AUTH_TOKEN=USER_TOKEN, DATABASE_URL=None),
        )
        assert proc.returncode != 0
        assert "[ERROR] DATABASE_URL is not set" in proc.stderr

    def test_control_set_database_url_starts(self):
        proc, tools = _tools_run(_env(MCP_AUTH_TOKEN=USER_TOKEN))
        assert "CONFIG_ERROR" not in proc.stdout, proc.stdout
        assert "get_menu" in tools


class TestAdminToolsFlagEdgeCases:
    @pytest.mark.parametrize("value", ["", "   ", "on"])
    def test_blank_or_on_with_admin_token_starts(self, value):
        proc, tools = _tools_run(
            _env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOKEN=ADMIN_TOKEN, MCP_ADMIN_TOOLS=value)
        )
        assert proc.returncode == 0, proc.stderr
        assert "CONFIG_ERROR" not in proc.stdout, proc.stdout
        assert tools >= ADMIN_TOOLS
        assert "ADMIN_CONFIGURED True" in proc.stdout

    @pytest.mark.parametrize("value", ["0", "false", "NO", " off ", "off"])
    def test_falsy_with_admin_token_is_config_error(self, value):
        proc, tools = _tools_run(
            _env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOKEN=ADMIN_TOKEN, MCP_ADMIN_TOOLS=value)
        )
        assert "CONFIG_ERROR" in proc.stdout, proc.stdout + proc.stderr
        assert "MCP_ADMIN_TOOLS" in proc.stdout
        assert not tools

    def test_whitespace_admin_token_means_not_configured(self):
        proc, tools = _tools_run(_env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOKEN="   "))
        assert proc.returncode == 0, proc.stderr
        assert "ADMIN_CONFIGURED False" in proc.stdout
        assert not (ADMIN_TOOLS & tools)

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_tools_without_admin_token_keeps_admin_tools_off(self, value):
        proc, tools = _tools_run(_env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOOLS=value))
        assert proc.returncode == 0, proc.stderr
        assert "ADMIN_CONFIGURED False" in proc.stdout
        assert tools and not (ADMIN_TOOLS & tools)

    def test_control_explicit_on_without_admin_token_has_admin_tools(self):
        _, tools = _tools_run(_env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOOLS="on"))
        assert tools >= ADMIN_TOOLS


class TestSecondAppGuard:
    @staticmethod
    def _snippet(second_admin: bool) -> str:
        second = (
            f"remote_server.create_remote_app(token='{USER_TOKEN}', admin_token='{ADMIN_TOKEN}')\n"
            if second_admin
            else f"remote_server.create_remote_app(token='{USER_TOKEN}')\n"
        )
        return (
            "from dialogflow_mcp import remote_server\n"
            f"remote_server.create_remote_app(token='{USER_TOKEN}', admin_token='{ADMIN_TOKEN}')\n"
            "try:\n"
            f"    {second}"
            "    print('SECOND_OK')\n"
            "except remote_server.ConfigError as exc:\n"
            "    print('CONFIG_ERROR', exc)\n"
        )

    def test_second_app_without_admin_token_refused(self):
        # Neither token is in the environment: with MCP_ADMIN_TOKEN there, the
        # second call would resolve it and never reach the no-admin-token branch.
        proc = _run(["-c", self._snippet(False)], _env())
        assert proc.returncode == 0, proc.stderr
        assert "CONFIG_ERROR" in proc.stdout and "fresh process" in proc.stdout
        assert "SECOND_OK" not in proc.stdout
        for token in (USER_TOKEN, ADMIN_TOKEN):
            assert token not in proc.stdout
            assert token not in proc.stderr

    def test_control_second_app_with_admin_token_allowed(self):
        proc = _run(["-c", self._snippet(True)], _env())
        assert "SECOND_OK" in proc.stdout, proc.stdout + proc.stderr

    def test_control_several_apps_without_admin_token_allowed(self):
        snippet = (
            "from dialogflow_mcp import remote_server\n"
            f"remote_server.create_remote_app(token='{USER_TOKEN}')\n"
            f"remote_server.create_remote_app(token='{USER_TOKEN}')\n"
            "print('BOTH_OK')\n"
        )
        proc = _run(["-c", snippet], _env())
        assert "BOTH_OK" in proc.stdout, proc.stdout + proc.stderr


class TestConfigErrorsDoNotLeakTokens:
    SHORT_USER = "Qz7k"
    SHORT_ADMIN = "Wm4p"
    SAME = "same-token-0123456789abcdef"

    @pytest.mark.parametrize(
        "env,secrets",
        [
            ({"MCP_AUTH_TOKEN": SHORT_USER}, [SHORT_USER]),
            (
                {"MCP_AUTH_TOKEN": USER_TOKEN, "MCP_ADMIN_TOKEN": SHORT_ADMIN},
                [USER_TOKEN, SHORT_ADMIN],
            ),
            ({"MCP_AUTH_TOKEN": SAME, "MCP_ADMIN_TOKEN": SAME}, [SAME]),
            (
                {
                    "MCP_AUTH_TOKEN": USER_TOKEN,
                    "MCP_ADMIN_TOKEN": ADMIN_TOKEN,
                    "MCP_ADMIN_TOOLS": "false",
                },
                [USER_TOKEN, ADMIN_TOKEN],
            ),
        ],
    )
    def test_error_text_has_no_token_value(self, env, secrets):
        proc, _ = _tools_run(_env(**env))
        assert "CONFIG_ERROR" in proc.stdout, proc.stdout + proc.stderr
        for secret in secrets:
            assert secret not in proc.stdout
            assert secret not in proc.stderr


class TestSharedTokenBanner:
    LINE = "[WARN]    Every MCP_AUTH_TOKEN holder has admin rights"

    @staticmethod
    def _main(env: dict[str, str]) -> subprocess.CompletedProcess:
        snippet = (
            "import uvicorn\n"
            "uvicorn.run = lambda *a, **k: None\n"
            "from dialogflow_mcp import remote_server\n"
            "remote_server.main()\n"
        )
        return _run(["-c", snippet], env)

    def test_shared_token_with_admin_tools_warns(self):
        proc = self._main(_env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOOLS="on"))
        assert proc.returncode == 0, proc.stderr
        assert self.LINE in proc.stdout
        assert "set MCP_ADMIN_TOKEN to separate them" in proc.stdout

    def test_admin_token_no_warning(self):
        proc = self._main(
            _env(MCP_AUTH_TOKEN=USER_TOKEN, MCP_ADMIN_TOKEN=ADMIN_TOKEN, MCP_ADMIN_TOOLS="on")
        )
        assert proc.returncode == 0, proc.stderr
        assert self.LINE not in proc.stdout

    def test_admin_tools_off_no_warning(self):
        proc = self._main(_env(MCP_AUTH_TOKEN=USER_TOKEN))
        assert proc.returncode == 0, proc.stderr
        assert self.LINE not in proc.stdout
