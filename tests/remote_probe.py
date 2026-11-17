"""Out-of-process probe for the remote server (run by tests/test_unit.py).

remote_server mutates the singleton FastMCP from server_es, and FastMCP's
StreamableHTTPSessionManager can only be run once per process; server_es also
reads MCP_ADMIN_TOOLS at import time. Each probe therefore runs in a fresh
interpreter and prints a single JSON object on its last stdout line.

Usage: python tests/remote_probe.py <streamable-http|sse>
"""

from __future__ import annotations

import json
import os
import sys

from starlette.testclient import TestClient

from dialogflow_mcp import remote_server

# The remote server requires DATABASE_URL. Nothing listens on port 9, so the
# probe never reaches a real database (and /ready reports it as down).
os.environ.setdefault("DATABASE_URL", "postgresql://probe:probe@127.0.0.1:9/probe_unused")

TOKEN = "probe-token-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MCP_HEADERS = {
    **AUTH,
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _parse_mcp_body(resp) -> dict | None:
    """Return the JSON-RPC message from a JSON or SSE response body."""
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        return resp.json()
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    return None


def probe_streamable() -> dict:
    out: dict = {}
    app = remote_server.create_remote_app(token=TOKEN)
    out["transport"] = app.state.mcp_transport
    out["allowed_hosts"] = app.state.allowed_hosts

    with TestClient(app, base_url="http://localhost") as client:
        # /health is public.
        r = client.get("/health")
        out["health_status"] = r.status_code
        out["health_body"] = r.json()

        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest-probe", "version": "0"},
            },
        }
        no_auth = {k: v for k, v in MCP_HEADERS.items() if k != "Authorization"}
        r = client.post("/mcp", json=init_req, headers=no_auth)
        out["noauth_status"] = r.status_code
        out["noauth_www_authenticate"] = r.headers.get("www-authenticate")

        r = client.post(
            "/mcp",
            json=init_req,
            headers={**MCP_HEADERS, "Authorization": "Bearer wrong-token-0123456789"},
        )
        out["wrongtoken_status"] = r.status_code

        r = client.post(
            "/mcp",
            json=init_req,
            headers={**MCP_HEADERS, "Authorization": TOKEN},  # missing "Bearer "
        )
        out["nobearer_status"] = r.status_code

        # Correct token but a Host that is not in the allowlist -> 421.
        r = client.post("/mcp", json=init_req, headers={**MCP_HEADERS, "Host": "evil.example.com"})
        out["badhost_status"] = r.status_code

        # Happy path: initialize -> initialized -> tools/list.
        r = client.post("/mcp", json=init_req, headers=MCP_HEADERS)
        out["init_status"] = r.status_code
        init_msg = _parse_mcp_body(r)
        out["init_server_name"] = (
            (init_msg or {}).get("result", {}).get("serverInfo", {}).get("name")
        )
        session_id = r.headers.get("mcp-session-id")
        out["has_session_id"] = bool(session_id)
        sess_headers = {**MCP_HEADERS}
        if session_id:
            sess_headers["mcp-session-id"] = session_id
        proto = (init_msg or {}).get("result", {}).get("protocolVersion")
        if proto:
            sess_headers["mcp-protocol-version"] = proto

        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=sess_headers,
        )
        out["initialized_status"] = r.status_code

        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=sess_headers,
        )
        out["tools_status"] = r.status_code
        tools_msg = _parse_mcp_body(r) or {}
        out["tools"] = sorted(t["name"] for t in tools_msg.get("result", {}).get("tools", []))
    return out


def probe_sse() -> dict:
    out: dict = {}
    app = remote_server.create_remote_app(token=TOKEN, transport="sse")
    out["transport"] = app.state.mcp_transport
    out["paths"] = sorted(getattr(r, "path", "") for r in app.routes)
    with TestClient(app, base_url="http://localhost") as client:
        out["health_status"] = client.get("/health").status_code
        out["health_body"] = client.get("/health").json()
        out["sse_noauth_status"] = client.get("/sse").status_code
        out["messages_noauth_status"] = client.post("/messages/", json={}).status_code
    return out


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "streamable-http"
    result = probe_sse() if mode == "sse" else probe_streamable()
    print(json.dumps(result))
