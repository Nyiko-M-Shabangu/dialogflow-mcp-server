# ADR 0008: Plain single-line log handler in remote mode

- Status: Accepted
- Date: 2026-11-17

## Context

Operators need to search hosted logs for security events, such as a refused admin call, by a single pattern like `admin_tool_denied tool=<name> role=<role>`. That only works if each event is one physical line.

Importing `server_es` constructs `FastMCP`, which configures logging and installs a Rich handler on the root logger (as of `mcp` 1.30.0). The Rich handler wraps at 80 columns and adds a `file:line` column. A long message lands on two or more lines in a hosted log view, so a search for the full pattern fails, while a `caplog` test still passes. uvicorn's own `uvicorn.*` loggers do not propagate to the root logger and are not affected.

Alternatives considered:

- Configure logging in `main()` only. `uvicorn --factory` and in-process test probes never call `main()`, so tests would not exercise the deployed configuration.
- JSON logs, one object per line. Better for a log drain, but nothing consumes them yet and it changes every existing line.
- Keep Rich and document a looser search. Leaves "one line per event" false.

## Decision

In `create_remote_app()`, right after `server_es` is imported, replace the root handler:

```python
logging.basicConfig(
    force=True,
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
```

In-process test calls of `create_remote_app()` that raise before the `server_es` import never replace the test process's logging. Tests that build a full app run in subprocesses.

## Consequences

- In remote mode, every non-uvicorn log line is plain text on stderr with a level field. Stdio servers keep FastMCP's Rich output.
- Some hosts colour stderr lines as errors. If that is a nuisance, switch the stream to stdout, and check any test probe that reads the last stdout line as its result.
- To reverse: delete the `basicConfig` call; FastMCP's handler returns.

Implementation notes: built as specified, in `create_remote_app()` in [`remote_server.py`](../../src/dialogflow_mcp/remote_server.py), right after `server_es` is imported. The runtime dependency is now `mcp` without the `[cli]` extra, so the Docker image does not install Rich at all; the `basicConfig` call keeps the format the same either way. The startup banner still goes to stdout with `print`. [`tests/test_remote_auth.py`](../../tests/test_remote_auth.py) checks, in a subprocess that built the remote app, that a refused admin call produces exactly one stderr line `... WARNING dialogflow_mcp.server_es admin_tool_denied tool=update_order_status role=none` with no Rich `file:line` column and no token.
