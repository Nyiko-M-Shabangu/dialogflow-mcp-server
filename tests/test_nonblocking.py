"""Regression: a slow Dialogflow or Postgres call must not block the event loop.

FastMCP (mcp 1.x) awaits `async def` tools but calls plain `def` tools inline
on the event loop. Every tool here does blocking I/O (psycopg, gRPC), which
would stall /health and every other client if it ran on the loop. The tools
are `async def` and run their blocking work in a worker thread; these tests
pin that down.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import anyio

from dialogflow_mcp import server, server_es

# Long enough that scheduling noise on a busy CI runner stays well inside the margins.
SLOW_S = 1.0
PROBE = str(Path(__file__).resolve().parent / "blocking_probe.py")


def _measure(mcp, calls: list[tuple[str, dict]]) -> tuple[float, float, list]:
    """Run the tool calls concurrently next to a 20 ms heartbeat task.

    Returns (elapsed seconds, longest heartbeat gap, results)."""

    async def main():
        results: list = []
        done = anyio.Event()
        max_gap = 0.0

        async def heartbeat():
            nonlocal max_gap
            last = time.monotonic()
            while not done.is_set():
                await anyio.sleep(0.02)
                now = time.monotonic()
                max_gap = max(max_gap, now - last)
                last = now

        async def run_all():
            async with anyio.create_task_group() as inner:
                for name, args in calls:

                    async def one(name=name, args=args):
                        results.append(await mcp.call_tool(name, args))

                    inner.start_soon(one)
            done.set()

        start = time.monotonic()
        async with anyio.create_task_group() as tg:
            tg.start_soon(heartbeat)
            tg.start_soon(run_all)
        return time.monotonic() - start, max_gap, results

    return anyio.run(main)


def test_tools_are_coroutine_functions():
    import inspect

    for mcp in (server.mcp, server_es.mcp):
        for tool in mcp._tool_manager.list_tools():
            assert inspect.iscoroutinefunction(tool.fn), tool.name


def test_es_db_tools_do_not_block_event_loop(monkeypatch):
    def slow_fetch_all(_query, _params=None):
        time.sleep(SLOW_S)
        return []

    monkeypatch.setattr(server_es, "fetch_all", slow_fetch_all)
    elapsed, gap, results = _measure(
        server_es.mcp, [("get_menu", {}), ("get_food_item_details", {"food_name": "Kota"})]
    )
    assert len(results) == 2
    assert gap < SLOW_S / 2, f"event loop stalled for {gap:.2f}s"
    # get_food_item_details does two lookups (exact, then substring): 2 x SLOW_S.
    assert elapsed < 3 * SLOW_S, f"calls did not overlap ({elapsed:.2f}s)"


def test_es_detect_intent_does_not_block_event_loop(monkeypatch):
    from google.cloud import dialogflow_v2 as df

    class SlowClient:
        def detect_intent(self, request):
            time.sleep(SLOW_S)
            return df.DetectIntentResponse(query_result=df.QueryResult(fulfillment_text="Hi"))

    monkeypatch.setattr(server_es, "_sessions_client", lambda: SlowClient())
    monkeypatch.setattr(server_es, "execute", lambda *_a, **_k: 1)
    monkeypatch.setattr(server_es, "PROJECT", "demo-project")
    elapsed, gap, results = _measure(
        server_es.mcp, [("detect_intent", {"text": "hi"}), ("detect_intent", {"text": "hello"})]
    )
    assert len(results) == 2
    assert gap < SLOW_S / 2, f"event loop stalled for {gap:.2f}s"
    assert elapsed < 1.7 * SLOW_S, f"calls did not overlap ({elapsed:.2f}s)"


def test_cx_detect_intent_does_not_block_event_loop(monkeypatch):
    from google.cloud import dialogflowcx_v3 as cx

    class SlowCxClient:
        def session_path(self, project, location, agent, session):
            return f"projects/{project}/locations/{location}/agents/{agent}/sessions/{session}"

        def detect_intent(self, request):
            time.sleep(SLOW_S)
            return cx.DetectIntentResponse(query_result=cx.QueryResult())

    monkeypatch.setenv("GCP_PROJECT", "demo-project")
    monkeypatch.setenv("DF_AGENT_ID", "demo-agent")
    monkeypatch.setattr(server, "_sessions_client", lambda _location: SlowCxClient())
    elapsed, gap, results = _measure(
        server.mcp, [("detect_intent", {"text": "hi"}), ("detect_intent", {"text": "yo"})]
    )
    assert len(results) == 2
    assert gap < SLOW_S / 2, f"event loop stalled for {gap:.2f}s"
    assert elapsed < 1.7 * SLOW_S, f"calls did not overlap ({elapsed:.2f}s)"


def test_remote_health_and_second_client_not_blocked_by_slow_tool():
    """End to end with the real Starlette app served by uvicorn (subprocess)."""
    env = dict(os.environ)
    for var in ("MCP_ADMIN_TOOLS", "MCP_ADMIN_TOKEN", "MCP_TRANSPORT", "GCP_PROJECT"):
        env.pop(var, None)
    env["MCP_AUTH_TOKEN"] = "user-token-0123456789abcdef"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, PROBE],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    out = json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][-1])
    slow = out["slow_s"]

    assert out["health_status"] == 200
    assert [c["status"] for c in out["calls"]] == [200, 200]
    assert all(c["is_error"] is False for c in out["calls"]), out
    # /health must answer while both tool calls are still sleeping.
    assert out["health_latency_s"] < slow / 2, out
    # The two sessions' calls overlap instead of running back to back.
    # Back to back would take 2 x slow; leave room for a busy machine.
    assert out["both_calls_s"] < 1.8 * slow, out
