# ADR 0004: Keep tools/list unfiltered and enforce the role inside the tools

- Status: Accepted
- Date: 2026-11-17

## Context

When an admin token is configured, the three admin tools are registered for the whole process. User-token clients therefore see them in `tools/list`, and get a refusal if they call them. The alternative is to hide them per role.

Alternatives considered:

- **Per-request filtering.** Override the low-level `list_tools` handler to read the request role. This couples the server to FastMCP internals. On Streamable HTTP the client caches the list per session, while the role is evaluated per HTTP request, so the cached list could be wrong for a later request.
- **Two FastMCP instances** (user and admin) mounted on different paths. This doubles the session managers and lifespans and changes client URLs.

## Decision

Keep a single, unfiltered tool list. Enforce the role inside each admin tool through the `admin_tool` wrapper and `_admin_denied()` in [`server_es.py`](../../src/dialogflow_mcp/server_es.py). Hiding tool names is not a security control; the per-request check is.

## Consequences

- Some LLM clients may try an admin tool with a user token. They get a clear refusal that says the admin token is needed.
- The README should say that admin tools are listed for every caller.
- The strength of the role check matters more because the names are visible. See [ADR 0001](0001-fail-closed-admin-check.md).
- To change: implement per-request filtering if users are confused by the refusals or a review requires hiding the names.

Implementation notes: in place. Admin tools are registered through the `admin_tool` decorator ([ADR 0001](0001-fail-closed-admin-check.md)) when `MCP_ADMIN_TOOLS` is on (forced on when `MCP_ADMIN_TOKEN` is set), and each one returns an error dict for callers without the admin role. The README says that admin tools are listed for every caller.
