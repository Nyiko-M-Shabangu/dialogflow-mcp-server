"""Dialogflow MCP Server — expose a Dialogflow agent as an MCP tool.

Server modules are imported lazily inside each entry point so that, e.g., the
CX entry point does not build the ES server or import psycopg.
"""


def main() -> None:
    """Entry point for the Dialogflow CX MCP server (stdio)."""
    try:
        from dialogflow_mcp.server import mcp as _mcp_cx
    except ImportError as exc:
        if "dialogflowcx" in str(exc):
            raise SystemExit(
                "The CX server needs the 'cx' extra: "
                "pip install 'dialogflow-mcp-server[cx]' (or uv sync, which installs it for development)."
            ) from None
        raise

    _mcp_cx.run()


def main_es() -> None:
    """Entry point for the Dialogflow ES MCP server (stdio)."""
    from dialogflow_mcp.server_es import mcp as _mcp_es

    _mcp_es.run()


def main_remote() -> None:
    """Entry point for the authenticated remote HTTP MCP server."""
    from dialogflow_mcp.remote_server import main as _remote_main

    _remote_main()
