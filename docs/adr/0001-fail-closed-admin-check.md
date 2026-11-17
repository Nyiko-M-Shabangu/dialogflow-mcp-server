# ADR 0001: Fail-closed admin check in remote mode, admin tools registered by construction

- Status: Accepted
- Date: 2026-11-17

## Context

Before this decision, `_admin_denied()` in [`server_es.py`](../../src/dialogflow_mcp/server_es.py) read `request.state.mcp_role` from the MCP request context, and when it found no request (stdio, direct Python calls, or any SDK path that does not attach the HTTP request), it allowed the call. That is right for stdio. In remote mode it is fail-open: if a future SDK version stopped attaching the request, every user-token caller would get the admin tools and full customer details from `track_order`. The dependency range was `mcp[cli]<2` (now `mcp>=1.30,<2`), so such a change could arrive with a minor upgrade.

Second, each admin tool called the check itself. An admin tool added later without that call would have been open to every caller.

Alternatives considered:

- Keep the code as it was. It relies on the SDK and on every author remembering the call.
- Pin a minimum SDK version. This narrows the risk but still fails open on any new path.
- Enforce roles in the middleware by parsing JSON-RPC `tools/call` bodies. This means buffering streaming bodies and duplicating protocol parsing for both transports.

## Decision

1. `create_remote_app()` turns on a process-wide "request role required" flag. With the flag on, a missing request context means **refuse**. Stdio and direct calls keep the flag off, so a call without a request is still allowed there.
2. An `admin_tool` decorator becomes the only way to register an admin tool. It wraps the function with the check and one WARNING log line per refusal, records the tool in an admin list, and registers it with FastMCP when admin tools are enabled.
3. An inventory test pins the registered tool names to two literal sets, public and admin. It calls every admin tool with a user-role request and asserts a refusal before any database access. An unclassified tool fails CI.

## Consequences

- In remote mode, a broken request path makes admin tools refuse for everyone and makes `track_order` hide customer fields. That is safe and visible in tests; it costs only admin availability.
- The flag is process-global. Tests that build the remote app must run in a subprocess or set the flag with `monkeypatch`.
- Adding any tool means editing the inventory test. This is deliberate: someone must decide whether a tool is public or admin.
- The inventory test reads FastMCP's tool registry through a private attribute. A rename in the SDK fails the test loudly.
- To reverse: remove the flag call from `create_remote_app()`. The decorator and the test are behaviour-neutral and can stay.

Implementation notes: built in [`server_es.py`](../../src/dialogflow_mcp/server_es.py) and [`remote_server.py`](../../src/dialogflow_mcp/remote_server.py).

- `create_remote_app()` calls `server_es.require_request_role()`, which sets `_REMOTE_ROLE_REQUIRED`. `_admin_denied()` then refuses a call with no request, a request without state, or a role other than the exact string `admin`.
- `admin_tool` is an `async` wrapper: the check runs on the event loop, where the request context is set, before any blocking work goes to a worker thread. A refusal never touches the database and logs `admin_tool_denied tool=<name> role=<role|none>` at WARNING.
- `track_order` uses the same check to decide whether customer fields are returned ([ADR 0002](0002-pii-allowlist.md)).
- Tests: [`tests/test_admin_gate.py`](../../tests/test_admin_gate.py) covers every branch of the check, the decorator, the log line, and the inventory (in a subprocess: every registered tool is in `PUBLIC_TOOLS` or `ADMIN_TOOLS`, every admin tool refuses a user request without touching the database, and the tool input schemas equal the snapshot in `tests/tool_schemas_base.json`). [`tests/test_remote_auth.py`](../../tests/test_remote_auth.py) proves over HTTP, on both transports, that a user token is refused and an admin token passes, and that a direct call after `create_remote_app()` is refused with exactly one plain log line.
