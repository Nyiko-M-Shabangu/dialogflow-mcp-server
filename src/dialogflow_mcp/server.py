# server.py — Dialogflow CX MCP Server
#
# A thin MCP server that wraps the Dialogflow CX API so any
# MCP-compatible client can drive a CX agent conversationally.

import functools
import os
import threading
import uuid

import anyio
from google.api_core.client_options import ClientOptions
from google.cloud import dialogflowcx_v3 as cx
from google.protobuf.json_format import MessageToDict
from mcp.server.fastmcp import FastMCP

from dialogflow_mcp.gcp_auth import client_kwargs

# ── Config ──────────────────────────────────────────────────
PROJECT = os.environ.get("GCP_PROJECT", "")
LOCATION = os.environ.get("DF_LOCATION", "global")
AGENT_ID = os.environ.get("DF_AGENT_ID", "")

mcp = FastMCP("dialogflow-cx")


# ── Helpers ─────────────────────────────────────────────────
_client: cx.SessionsClient | None = None
_client_endpoint: str | None = None
_client_lock = threading.Lock()


def _require_config() -> tuple[str, str, str]:
    """Return (project, location, agent_id), validated at call time."""
    project = os.environ.get("GCP_PROJECT", PROJECT).strip()
    location = (os.environ.get("DF_LOCATION", LOCATION) or "global").strip()
    agent_id = os.environ.get("DF_AGENT_ID", AGENT_ID).strip()
    missing = [
        name for name, value in (("GCP_PROJECT", project), ("DF_AGENT_ID", agent_id)) if not value
    ]
    if missing:
        raise ValueError(
            "Dialogflow CX is not configured: missing environment variable(s) "
            + ", ".join(missing)
            + ". Set GCP_PROJECT to your Google Cloud project ID and DF_AGENT_ID "
            "to the CX agent ID (optionally DF_LOCATION, default 'global')."
        )
    return project, location, agent_id


def _sessions_client(location: str) -> cx.SessionsClient:
    """Return a cached SessionsClient for the correct regional endpoint."""
    global _client, _client_endpoint
    endpoint = (
        "dialogflow.googleapis.com"
        if location == "global"
        else f"{location}-dialogflow.googleapis.com"
    )
    with _client_lock:
        if _client is None or _client_endpoint != endpoint:
            # Credentials from GOOGLE_APPLICATION_CREDENTIALS_JSON if set, else ADC.
            _client = cx.SessionsClient(
                client_options=ClientOptions(api_endpoint=endpoint),
                **client_kwargs(),
            )
            _client_endpoint = endpoint
        return _client


# ── Tool ────────────────────────────────────────────────────
@mcp.tool()
async def detect_intent(
    text: str,
    session_id: str | None = None,
    language_code: str = "en",
) -> dict:
    """Send a user message to the Dialogflow CX agent and return
    the agent's reply, matched intent, active page, and extracted
    parameters. Pass the returned session_id back on subsequent
    calls to maintain conversational context."""
    # The gRPC call blocks; FastMCP would run a plain `def` tool inline on the
    # event loop, so run it in a worker thread (the client is thread-safe).
    return await anyio.to_thread.run_sync(
        functools.partial(_detect_intent, text, session_id, language_code)
    )


def _detect_intent(text: str, session_id: str | None, language_code: str) -> dict:
    """Blocking body of detect_intent."""
    project, location, agent_id = _require_config()
    sid = session_id or str(uuid.uuid4())
    client = _sessions_client(location)
    session_path = client.session_path(project, location, agent_id, sid)

    response = client.detect_intent(
        request=cx.DetectIntentRequest(
            session=session_path,
            query_input=cx.QueryInput(
                text=cx.TextInput(text=text),
                language_code=language_code,
            ),
        )
    )

    result = response.query_result

    return {
        "session_id": sid,
        "replies": [
            " ".join(msg.text.text)
            for msg in result.response_messages
            if msg.text and msg.text.text
        ],
        "intent": (
            result.match.intent.display_name if result.match and result.match.intent else None
        ),
        "confidence": (result.match.confidence if result.match else None),
        "page": (result.current_page.display_name if result.current_page else None),
        "parameters": MessageToDict(result._pb).get("parameters", {}),
    }


if __name__ == "__main__":
    mcp.run()
