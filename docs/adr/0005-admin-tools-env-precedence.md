# ADR 0005: MCP_ADMIN_TOOLS and MCP_ADMIN_TOKEN precedence; a blank value means unset

- Status: Accepted
- Date: 2026-11-17

## Context

`server_es` reads `MCP_ADMIN_TOOLS` once, at import. `remote_server` sets it to `off` only when the variable is absent, so an empty value counts as "explicitly set". With an admin token configured, that combination is a startup error. A common case hits it: `uv run --env-file .env` with a `.env` file that contains `MCP_ADMIN_TOOLS=` and a valid `MCP_ADMIN_TOKEN`. The operator never meant "off".

A second hazard: with an admin token, `create_remote_app()` writes `MCP_ADMIN_TOOLS=on` into the process environment so that `server_es` registers the admin tools at import. If the same process later calls `create_remote_app()` again *without* an admin token, that call reads `on` and treats the shared user token as admin. No deployed path does this (`main()` and `uvicorn --factory` build the app once per process), but nothing prevents it.

Alternatives considered:

- Reject unknown `MCP_ADMIN_TOOLS` values (such as `enabled`) and non-ASCII tokens at startup. This could stop existing deployments from starting, so it would need a release note first.
- Let `MCP_ADMIN_TOKEN` always win and silently ignore `MCP_ADMIN_TOOLS=off`. This hides a contradiction the operator should resolve.
- Stop writing the environment and register admin tools through a function, or build a fresh `FastMCP` per app. This is a larger restructure of import-time registration.

## Decision

1. A blank or whitespace-only `MCP_ADMIN_TOOLS` counts as unset. It is normalised to `off` before `server_es` is imported.
2. An explicit falsy value (`off`, `0`, `false`, `no`, any case) together with an admin token stays a startup error.
3. A module flag records that an app was built with an admin token. A later `create_remote_app()` call in the same process without an admin token raises `ConfigError`. Several apps without an admin token in one process remain allowed.

## Consequences

- A start driven by an `.env` file with a blank `MCP_ADMIN_TOOLS` works.
- In remote mode, `MCP_ADMIN_TOOLS=enabled` keeps meaning "off". This must be documented.
- The one sequence that could silently make the user token admin fails loudly.
- To reverse: remove the blank normalisation at the top of `remote_server.py`, and the module flag and its check in `create_remote_app()`.

Implementation notes: built in [`remote_server.py`](../../src/dialogflow_mcp/remote_server.py). At import, a blank or whitespace-only `MCP_ADMIN_TOOLS` is treated as unset and set to `off`. `_ADMIN_TOOLS_FORCED_ON` records that an app was built with an admin token, and a later `create_remote_app()` without one raises `ConfigError` ("start the remote server in a fresh process"). Related, same change: the startup banner warns when the shared token holds admin rights (admin tools on, no admin token). Tests in [`tests/test_remote_auth.py`](../../tests/test_remote_auth.py) run each case in a fresh interpreter: blank and `on` values with an admin token start; `0`, `false`, `NO`, ` off ` with an admin token fail; a whitespace admin token counts as unset; the second-app sequence fails while several apps without an admin token stay allowed; and no error message contains a token value.
