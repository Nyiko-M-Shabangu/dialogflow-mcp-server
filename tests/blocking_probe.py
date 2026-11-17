"""Out-of-process probe: does a slow tool call block the event loop?
(run by tests/test_nonblocking.py).

Serves the real remote app (streamable-http) with uvicorn on a free localhost
port, replaces the database helper used by get_menu with one that sleeps
SLOW_S seconds (a stand-in for a slow Postgres query or Dialogflow call), then:

* starts two get_menu calls from two separate MCP sessions at the same time,
* while they run, requests GET /health and times it.

If tools run inline on the event loop, /health waits for a tool to finish and
the two calls run one after the other (about 2 x SLOW_S). If tools run in a
worker thread, /health answers at once and the calls overlap (about SLOW_S).

Prints one JSON object on its last stdout line.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time

import anyio
import httpx
import uvicorn

from dialogflow_mcp import remote_server

# The remote server requires DATABASE_URL. Nothing listens on port 9, so the
# probe never reaches a real database (and /ready reports it as down).
os.environ.setdefault("DATABASE_URL", "postgresql://probe:probe@127.0.0.1:9/probe_unused")

SLOW_S = 1.5
TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
PROTOCOL_VERSION = "2025-06-18"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INIT_REQ = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "pytest-blocking-probe", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
CALL_MENU = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": "get_menu", "arguments": {}},
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _parse_body(resp: httpx.Response) -> dict | None:
    if resp.headers.get("content-type", "").startswith("application/json"):
        return resp.json()
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    return None


async def _session(client: httpx.AsyncClient) -> dict[str, str]:
    headers = dict(HEADERS)
    r = await client.post("/mcp", json=INIT_REQ, headers=headers)
    r.raise_for_status()
    sid = r.headers.get("mcp-session-id")
    if sid:
        headers["mcp-session-id"] = sid
    headers["mcp-protocol-version"] = PROTOCOL_VERSION
    await client.post("/mcp", json=INITIALIZED, headers=headers)
    return headers


async def _probe(base: str) -> dict:
    out: dict = {"slow_s": SLOW_S, "calls": []}
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        sessions = [await _session(client), await _session(client)]
        start = time.monotonic()

        async def call(headers: dict[str, str]) -> None:
            r = await client.post("/mcp", json=CALL_MENU, headers=headers)
            body = _parse_body(r) or {}
            out["calls"].append(
                {
                    "status": r.status_code,
                    "is_error": body.get("result", {}).get("isError"),
                    "done_after_s": round(time.monotonic() - start, 3),
                }
            )

        async def health() -> None:
            await anyio.sleep(0.3)  # let both tool calls start first
            t0 = time.monotonic()
            r = await client.get("/health")
            out["health_status"] = r.status_code
            out["health_latency_s"] = round(time.monotonic() - t0, 3)

        async with anyio.create_task_group() as tg:
            for headers in sessions:
                tg.start_soon(call, headers)
            tg.start_soon(health)
        out["both_calls_s"] = round(time.monotonic() - start, 3)
    return out


def main() -> dict:
    app = remote_server.create_remote_app(transport="streamable-http")

    from dialogflow_mcp import server_es

    def slow_fetch_all(_query, _params=None):
        time.sleep(SLOW_S)  # blocking, like a real psycopg / gRPC call
        return []

    server_es.fetch_all = slow_fetch_all

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            timeout_graceful_shutdown=2,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)

    try:
        return anyio.run(_probe, f"http://127.0.0.1:{port}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    result = main()
    sys.stdout.flush()
    print(json.dumps(result))
