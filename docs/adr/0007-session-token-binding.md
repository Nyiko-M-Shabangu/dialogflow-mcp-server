# ADR 0007: Bind each MCP session to the credential that opened it

- Status: Proposed
- Date: 2026-11-17

## Context

The role of every tool call comes from the HTTP request that carries it. A user token cannot run admin tools on a session opened with the admin token, because the check runs per request. But the session itself is not tied to a credential. Any valid token holder who learns a live session ID can post into that session, delete it, or take over its GET stream.

The MCP Python SDK (verified with `mcp` 1.30.0) can bind a session to its creator. It does so only when `scope["user"]` is an `AuthenticatedUser`:

- **Streamable HTTP:** the session manager records the owner at creation. A POST, GET or DELETE on `/mcp` from a different credential gets **404** and a WARNING log line.
- **SSE:** the owner is recorded at `GET /sse`. A `POST /messages/` from a different credential gets **404**. A new `GET /sse` always opens a new session.
- If `scope["user"]` is absent or of another type, both sides compare as `None` and binding is a silent no-op. That is the state today.

Leaking a session ID is plausible. The SDK logs session IDs at INFO, and SSE carries them in the `/messages/?session_id=` query string, which reaches access logs. Not reproduced, but possible from the SDK code: a POST that reuses an in-flight JSON-RPC `id` might redirect the owner's response stream to the other caller.

Alternatives considered:

- Per-request role only (today). Accepts the residual above.
- A session-to-role map in our middleware. Duplicates SDK logic and must track session lifecycle.
- Lowering the SDK's session-manager loggers to WARNING. Reduces leaks on Streamable HTTP only, and can complement either option.

## Decision

Proposed: after a token matches, the middleware also sets `scope["user"] = AuthenticatedUser(AccessToken(token=<label>, client_id=<label>, scopes=[]))`, where `<label>` is `mcp-admin` or `mcp-user`, derived from the role and never from the token. The per-request admin check stays the primary control. Pin `mcp[cli]>=1.30,<2`, because on an older SDK the binding disappears without error.

## Consequences

- A request on a session opened with the other token gets 404 from the SDK, in both directions, plus one SDK WARNING line.
- Tests must prove the binding is active: cross-token POST, GET and DELETE return 404 where a no-op would return 200 or 202, and the object in `scope["user"]` must contain no token bytes.
- Clients that change token mid-session must re-initialise. Document this.
- Residual: all user-token holders share one label, so they are not separated from each other. Closing that needs per-client credentials.
- To reverse: stop setting `scope["user"]` and relax the pin.

Implementation notes: not built. `BearerAuthASGIMiddleware` in [`remote_server.py`](../../src/dialogflow_mcp/remote_server.py) sets only `state.mcp_role`; nothing sets `scope["user"]`, so the SDK's session binding is inactive. The dependency floor `mcp>=1.30,<2` that this decision needs is already in place. Listed in the README roadmap.
