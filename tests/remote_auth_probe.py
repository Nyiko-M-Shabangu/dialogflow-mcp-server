"""Out-of-process probe for remote admin-token roles and Host/Origin checks
(run by tests/test_remote_auth.py).

Like tests/remote_probe.py, every probe runs in a fresh interpreter (FastMCP
is a singleton, its session manager runs once per process, and server_es
reads MCP_ADMIN_TOOLS at import). Unlike remote_probe.py it serves the app
with a real uvicorn server on a free localhost port, so:

* the SSE transport can be driven end to end (GET /sse stream + POST
  /messages/), proving request.state.mcp_role is visible inside tools, and
* anything the server would log (e.g. a traceback) really reaches stderr,
  where the parent test can look for it. TestClient would re-raise instead.

track_order is called with both tokens to compare what each role sees. The
database helpers in server_es are replaced by a fixed fake order, so no
database is needed.

Tokens come from the environment (MCP_AUTH_TOKEN / MCP_ADMIN_TOKEN) so the
real env-driven configuration path is exercised. Prints one JSON object on its
last stdout line.

Usage: python tests/remote_auth_probe.py <streamable-http|sse>
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

USER_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
ADMIN_TOKEN = os.environ.get("MCP_ADMIN_TOKEN", "")
BAD_HOST = "evil.example.com"
PROTOCOL_VERSION = "2025-06-18"

INIT_REQ = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "pytest-auth-probe", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
# An invalid status is a DB-free discriminator: an admin caller gets
# "Invalid status" before any SQL runs, a non-admin gets the refusal.
CALL_ADMIN_TOOL = {
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
        "name": "update_order_status",
        "arguments": {"order_id": 1, "new_status": "not-a-status"},
    },
}


CALL_TRACK_ORDER = {
    "jsonrpc": "2.0",
    "id": 4,
    "method": "tools/call",
    "params": {"name": "track_order", "arguments": {"order_id": 7}},
}

FAKE_ORDER = {
    "order_id": 7,
    "customer_name": "Test Customer 7",
    "customer_phone": "+27 00 000 0007",
    "total_price": 40.0,
    "order_status": "preparing",
    "delivery_address": "7 Example Street, Testville",
    "created_at": None,
    "updated_at": None,
}


def _stub_database() -> None:
    """Serve track_order from FAKE_ORDER instead of Postgres."""
    from dialogflow_mcp import server_es

    def fake_fetch_one(*_args: object, **_kwargs: object) -> dict:
        return dict(FAKE_ORDER)

    def fake_fetch_all(*_args: object, **_kwargs: object) -> list:
        return [{"quantity": 1, "unit_price": 40.0, "total_price": 40.0, "item_name": "Kota"}]

    server_es.fetch_one = fake_fetch_one
    server_es.fetch_all = fake_fetch_all


def _order_keys(text: str | None) -> list[str] | str | None:
    try:
        return sorted(json.loads(text or "")["order"])
    except (ValueError, KeyError, TypeError):
        return text


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _tool_text(msg: dict | None) -> str | None:
    """The tools/call result content is JSON text; return that text."""
    if not msg or "result" not in msg:
        return json.dumps(msg)
    content = msg["result"].get("content") or []
    return content[0].get("text") if content else None


def _tool_names(msg: dict | None) -> list[str]:
    return sorted(t["name"] for t in (msg or {}).get("result", {}).get("tools", []))


def _parse_body(resp: httpx.Response) -> dict | None:
    if resp.headers.get("content-type", "").startswith("application/json"):
        return resp.json()
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    return None


# ── streamable-http ─────────────────────────────────────────
async def _streamable_session(client: httpx.AsyncClient, token: str) -> dict:
    out: dict = {}
    headers = {
        **_auth(token),
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    r = await client.post("/mcp", json=INIT_REQ, headers=headers)
    out["init_status"] = r.status_code
    sid = r.headers.get("mcp-session-id")
    if sid:
        headers["mcp-session-id"] = sid
    headers["mcp-protocol-version"] = PROTOCOL_VERSION
    await client.post("/mcp", json=INITIALIZED, headers=headers)
    r = await client.post("/mcp", json=LIST_TOOLS, headers=headers)
    out["tools"] = _tool_names(_parse_body(r))
    r = await client.post("/mcp", json=CALL_ADMIN_TOOL, headers=headers)
    out["call_status"] = r.status_code
    out["admin_call_text"] = _tool_text(_parse_body(r))
    r = await client.post("/mcp", json=CALL_TRACK_ORDER, headers=headers)
    out["track_order_keys"] = _order_keys(_tool_text(_parse_body(r)))
    return out


async def _probe_streamable(base: str) -> dict:
    out: dict = {}
    mcp_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        out["user"] = await _streamable_session(client, USER_TOKEN)
        if ADMIN_TOKEN:
            out["admin"] = await _streamable_session(client, ADMIN_TOKEN)

        r = await client.post(
            "/mcp",
            json=INIT_REQ,
            headers={**mcp_headers, **_auth("wrong-token-0123456789abcdef")},
        )
        out["wrongtoken_status"] = r.status_code
        r = await client.post(
            "/mcp",
            json=INIT_REQ,
            headers={**mcp_headers, **_auth(USER_TOKEN), "Host": BAD_HOST},
        )
        out["badhost_status"] = r.status_code
        out["badhost_body"] = r.text
        r = await client.post(
            "/mcp",
            json=INIT_REQ,
            headers={**mcp_headers, "Host": BAD_HOST},  # bad host AND no token
        )
        out["badhost_noauth_status"] = r.status_code
        r = await client.post(
            "/mcp",
            json=INIT_REQ,
            headers={**mcp_headers, **_auth(USER_TOKEN), "Origin": "http://evil.example.com"},
        )
        out["badorigin_status"] = r.status_code
        out["badorigin_body"] = r.text
        r = await client.get("/health", headers={"Host": "10.244.3.17:8000"})
        out["health_weird_host_status"] = r.status_code
    return out


# ── SSE ─────────────────────────────────────────────────────
async def _sse_session(client: httpx.AsyncClient, token: str) -> dict:
    out: dict = {}
    async with client.stream("GET", "/sse", headers=_auth(token)) as stream:
        out["sse_status"] = stream.status_code
        lines = stream.aiter_lines()

        async def next_event() -> tuple[str | None, str | None]:
            event = data = None
            async for line in lines:
                if line.startswith("event:"):
                    event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = line[len("data:") :].strip()
                elif line == "" and data is not None:
                    return event, data
            return event, data

        async def rpc(msg: dict) -> dict | None:
            r = await client.post(endpoint, json=msg, headers=_auth(token))
            out.setdefault("post_statuses", []).append(r.status_code)
            if "id" not in msg:
                return None
            with anyio.fail_after(30):
                while True:
                    event, data = await next_event()
                    if data is None:
                        return None
                    if event == "message":
                        parsed = json.loads(data)
                        if parsed.get("id") == msg["id"]:
                            return parsed

        with anyio.fail_after(30):
            event, endpoint = await next_event()
        out["endpoint_event"] = event
        init = await rpc(INIT_REQ)
        out["init_ok"] = bool(init and "result" in init)
        await rpc(INITIALIZED)
        out["tools"] = _tool_names(await rpc(LIST_TOOLS))
        out["admin_call_text"] = _tool_text(await rpc(CALL_ADMIN_TOOL))
        out["track_order_keys"] = _order_keys(_tool_text(await rpc(CALL_TRACK_ORDER)))
    return out


async def _probe_sse(base: str) -> dict:
    out: dict = {}
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        out["user"] = await _sse_session(client, USER_TOKEN)
        if ADMIN_TOKEN:
            out["admin"] = await _sse_session(client, ADMIN_TOKEN)

        r = await client.get("/sse", headers=_auth("wrong-token-0123456789abcdef"))
        out["wrongtoken_status"] = r.status_code
        # Bad Host with a valid token. The middleware must answer 421 itself:
        # the SDK's SSE transport would answer 421 and log a traceback.
        r = await client.get("/sse", headers={**_auth(USER_TOKEN), "Host": BAD_HOST})
        out["sse_badhost_status"] = r.status_code
        out["sse_badhost_body"] = r.text
        r = await client.post(
            "/messages/?session_id=00000000000000000000000000000000",
            json=INIT_REQ,
            headers={**_auth(USER_TOKEN), "Host": BAD_HOST},
        )
        out["messages_badhost_status"] = r.status_code
        r = await client.get(
            "/sse", headers={**_auth(USER_TOKEN), "Origin": "http://evil.example.com"}
        )
        out["sse_badorigin_status"] = r.status_code
        r = await client.get("/health", headers={"Host": "10.244.3.17:8000"})
        out["health_weird_host_status"] = r.status_code
    return out


def main(transport: str) -> dict:
    app = remote_server.create_remote_app(transport=transport)
    _stub_database()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",
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

    base = f"http://127.0.0.1:{port}"
    try:
        probe = _probe_sse if transport == "sse" else _probe_streamable
        out = anyio.run(probe, base)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    out["transport"] = app.state.mcp_transport
    out["admin_token_configured"] = app.state.admin_token_configured
    return out


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "streamable-http"
    result = main(mode)
    sys.stdout.flush()
    print(json.dumps(result))
