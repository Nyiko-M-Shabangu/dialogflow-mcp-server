# remote_server.py — Remote HTTP MCP Server with Bearer Token Authentication
#
# Exposes the Dialogflow ES + PostgreSQL MCP tools over HTTP using either the
# Streamable HTTP transport (default, endpoint /mcp) or the legacy SSE transport
# (endpoints /sse and /messages/).
# Keeps database credentials and GCP configurations securely on the server.
# MCP clients (Claude Desktop, Cursor, etc.) authenticate using an HTTP Bearer Token.
#
# Environment variables:
#   MCP_AUTH_TOKEN        (required) shared Bearer token, at least 16 characters.
#   DATABASE_URL          (required) Postgres connection string; the remote
#                         server refuses to start without it.
#   MCP_ADMIN_TOKEN       (optional) separate Bearer token for the admin tools,
#                         at least 16 characters and different from
#                         MCP_AUTH_TOKEN. Setting it registers the admin tools
#                         but only callers presenting this token may run them.
#   MCP_TRANSPORT         "streamable-http" (default) or "sse".
#   MCP_ALLOWED_HOSTS     comma-separated extra Host header values to accept,
#                         e.g. "mcp.example.com,mcp.example.com:443".
#   MCP_ALLOWED_ORIGINS   comma-separated extra Origin header values to accept.
#   MCP_ADMIN_TOOLS       defaults to "off" in remote mode; a blank value counts
#                         as unset.
#   HOST / PORT           bind address (default 0.0.0.0:8000).
#
# Run with:  dialogflow-mcp-remote
#   or:      uvicorn --factory dialogflow_mcp.remote_server:create_remote_app

from __future__ import annotations

import hmac
import logging
import os
import sys
from typing import TYPE_CHECKING, Any

from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:  # pragma: no cover
    from mcp.server.transport_security import (
        TransportSecurityMiddleware,
        TransportSecuritySettings,
    )
    from starlette.applications import Starlette

logger = logging.getLogger("dialogflow_mcp.remote")

# Remember whether the operator set MCP_ADMIN_TOOLS themselves, before we
# apply the remote default below (needed to detect "MCP_ADMIN_TOKEN set but
# MCP_ADMIN_TOOLS explicitly off"). A blank or whitespace-only value, such as
# an empty `MCP_ADMIN_TOOLS=` line in an env file, counts as unset.
_ADMIN_TOOLS_DEFAULTED = not os.environ.get("MCP_ADMIN_TOOLS", "").strip()

# Set once _enable_admin_tools_for_admin_token() has written
# MCP_ADMIN_TOOLS=on into the process environment. A later app without an
# admin token in the same process would read that value as "admin tools on for
# the shared token", so create_remote_app() refuses that sequence.
_ADMIN_TOOLS_FORCED_ON = False

# Remote mode defaults to admin tools OFF. This MUST be set before
# dialogflow_mcp.server_es is imported (it reads the flag at import time),
# which is why server_es is only imported lazily inside create_remote_app().
if _ADMIN_TOOLS_DEFAULTED:
    os.environ["MCP_ADMIN_TOOLS"] = "off"

# ── Server Configuration ─────────────────────────────────────
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

MIN_TOKEN_LENGTH = 16
VALID_TRANSPORTS = ("streamable-http", "sse")
# /health = liveness (no dependencies); /ready = readiness (DB + config).
# Both skip auth and the Host check so platform probes (e.g. Railway, which
# sends Host: healthcheck.railway.app) can reach them.
PUBLIC_PATHS = frozenset({"/health", "/ready"})

# Upper bound for the readiness database check, in seconds.
READY_TIMEOUT_S = 5.0

DEFAULT_ALLOWED_HOSTS = [
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
    "[::1]",
    "[::1]:*",
]
DEFAULT_ALLOWED_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
]


class ConfigError(RuntimeError):
    """Raised when the remote server is misconfigured."""


# ── Configuration helpers ────────────────────────────────────
def _split_env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _resolve_token(token: str | None = None) -> str:
    """Return a validated auth token (explicit arg, else MCP_AUTH_TOKEN)."""
    if token is None:
        token = os.environ.get("MCP_AUTH_TOKEN", "")
    token = token.strip()
    if not token:
        raise ConfigError(
            "MCP_AUTH_TOKEN is not set. Refusing to start an unauthenticated "
            "remote MCP server. Generate one with e.g. "
            '`python -c "import secrets; print(secrets.token_urlsafe(32))"`.'
        )
    if len(token) < MIN_TOKEN_LENGTH:
        raise ConfigError(
            f"MCP_AUTH_TOKEN is too short (must be at least {MIN_TOKEN_LENGTH} "
            "characters). Use a long random value, e.g. secrets.token_urlsafe(32)."
        )
    return token


def _resolve_admin_token(admin_token: str | None, auth_token: str) -> str | None:
    """Return the optional admin token (explicit arg, else MCP_ADMIN_TOKEN).

    None when unset/blank. Raises ConfigError when it is too short or equal to
    the regular auth token (that would hand admin rights to every client).
    """
    if admin_token is None:
        admin_token = os.environ.get("MCP_ADMIN_TOKEN", "")
    admin_token = admin_token.strip()
    if not admin_token:
        return None
    if len(admin_token) < MIN_TOKEN_LENGTH:
        raise ConfigError(
            f"MCP_ADMIN_TOKEN is too short (must be at least {MIN_TOKEN_LENGTH} "
            "characters). Use a long random value, e.g. secrets.token_urlsafe(32)."
        )
    if hmac.compare_digest(admin_token.encode("utf-8"), auth_token.encode("utf-8")):
        raise ConfigError(
            "MCP_ADMIN_TOKEN must differ from MCP_AUTH_TOKEN; otherwise every "
            "client holding the shared token would get admin rights."
        )
    return admin_token


def _enable_admin_tools_for_admin_token() -> None:
    """An admin token implies the admin tools are registered.

    Raises ConfigError on contradictory configuration: MCP_ADMIN_TOOLS set
    explicitly to a falsy value, or server_es already imported without them.
    """
    explicit_value = os.environ.get("MCP_ADMIN_TOOLS")
    explicitly_set = not (_ADMIN_TOOLS_DEFAULTED and explicit_value == "off")
    if explicit_value is not None and explicitly_set and not admin_tools_enabled():
        raise ConfigError(
            f"MCP_ADMIN_TOKEN is set but MCP_ADMIN_TOOLS={explicit_value!r} disables "
            "the admin tools. Unset MCP_ADMIN_TOKEN, or remove MCP_ADMIN_TOOLS / set "
            "it to 'on'."
        )
    already_loaded = sys.modules.get("dialogflow_mcp.server_es")
    if already_loaded is not None and not getattr(already_loaded, "ADMIN_TOOLS_ENABLED", False):
        raise ConfigError(
            "MCP_ADMIN_TOKEN is set but dialogflow_mcp.server_es was already imported "
            "with admin tools disabled; start the remote server in a fresh process."
        )
    global _ADMIN_TOOLS_FORCED_ON
    os.environ["MCP_ADMIN_TOOLS"] = "on"
    _ADMIN_TOOLS_FORCED_ON = True


def _resolve_transport(transport: str | None = None) -> str:
    if transport is None:
        transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    transport = transport.strip().lower()
    if transport not in VALID_TRANSPORTS:
        raise ConfigError(
            f"MCP_TRANSPORT={transport!r} is invalid; expected one of {VALID_TRANSPORTS}."
        )
    return transport


def _require_database_url() -> None:
    """The remote server never falls back to the local development database:
    a deploy without DATABASE_URL would otherwise start, pass /health and fail
    every tool call against 127.0.0.1."""
    if not os.environ.get("DATABASE_URL", "").strip():
        raise ConfigError(
            "DATABASE_URL is not set. The remote server needs a Postgres connection "
            "string (the local development default is only used by the stdio servers)."
        )


def _validate_gcp_credentials() -> None:
    """Fail at startup (not on the first detect_intent call) when
    GOOGLE_APPLICATION_CREDENTIALS_JSON is set but is not a usable key."""
    from dialogflow_mcp.gcp_auth import CredentialsConfigError, credentials_from_env

    try:
        credentials_from_env()
    except CredentialsConfigError as exc:
        raise ConfigError(str(exc)) from None


async def readiness_response() -> JSONResponse:
    """Readiness (GET /ready): the database answers SELECT 1 within
    READY_TIMEOUT_S and the settings the tools need are present; 503 otherwise.

    Reports only check names and ok/fail, never values or error text.
    """
    import anyio

    from dialogflow_mcp import database

    checks = {"database": "ok", "config": "ok"}
    try:
        with anyio.fail_after(READY_TIMEOUT_S):
            # abandon_on_cancel: return on timeout instead of waiting for the
            # worker thread; it only runs SELECT 1 and finishes on its own
            # (bounded by the pool's own timeout).
            await anyio.to_thread.run_sync(
                lambda: database.fetch_one("SELECT 1 AS ok"), abandon_on_cancel=True
            )
    except Exception:  # timeout, pool timeout, connection or SQL error
        logger.warning("readiness check: database unreachable")
        checks["database"] = "fail"
    if missing_runtime_config():
        checks["config"] = "incomplete"
    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        {"status": "ready" if ready else "unavailable", "checks": checks},
        status_code=200 if ready else 503,
    )


def missing_runtime_config() -> list[str]:
    """Settings the tools need that /health does not check.

    The server still starts without them (so a bare container passes a smoke
    test), but a deploy missing any of these is "healthy" while every tool call
    fails, so main() prints each problem as a startup warning.
    """
    problems = []
    if not os.environ.get("GCP_PROJECT", "").strip():
        problems.append("GCP_PROJECT is not set: detect_intent will return an error.")
    if not (
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    ):
        problems.append(
            "Neither GOOGLE_APPLICATION_CREDENTIALS_JSON nor "
            "GOOGLE_APPLICATION_CREDENTIALS is set: relying on ambient Google "
            "credentials (fine on GCP, fails on Railway)."
        )
    return problems


def build_allowed_hosts(extra: list[str] | None = None) -> list[str]:
    """Localhost defaults plus MCP_ALLOWED_HOSTS (or `extra`).

    A bare host without a port (e.g. "mcp.example.com") is also accepted with
    any explicit port ("mcp.example.com:*").
    """
    if extra is None:
        extra = _split_env_list("MCP_ALLOWED_HOSTS")
    hosts = list(DEFAULT_ALLOWED_HOSTS)
    for host in extra:
        candidates = [host]
        is_bracketed_ipv6 = host.startswith("[") and host.endswith("]")
        if ":" not in host or is_bracketed_ipv6:
            candidates.append(f"{host}:*")
        for candidate in candidates:
            if candidate not in hosts:
                hosts.append(candidate)
    return hosts


def build_allowed_origins(extra: list[str] | None = None) -> list[str]:
    if extra is None:
        extra = _split_env_list("MCP_ALLOWED_ORIGINS")
    origins = list(DEFAULT_ALLOWED_ORIGINS)
    for origin in extra:
        if origin not in origins:
            origins.append(origin)
    return origins


SHARED_TOKEN_ADMIN_WARNING = (
    "Every MCP_AUTH_TOKEN holder has admin rights and sees customer PII; "
    "set MCP_ADMIN_TOKEN to separate them."
)


def _shared_token_admin_warning(app: Any) -> str | None:
    """Startup warning for the shared-token mode: no admin token, admin tools on."""
    if not app.state.admin_token_configured and admin_tools_enabled():
        return SHARED_TOKEN_ADMIN_WARNING
    return None


def admin_tools_enabled() -> bool:
    return os.environ.get("MCP_ADMIN_TOOLS", "off").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# ── Pure ASGI Authentication Middleware ───────────────────────
# Note: We use pure ASGI instead of BaseHTTPMiddleware because
# BaseHTTPMiddleware can interfere with long-lived streaming connections.
class BearerAuthASGIMiddleware:
    """Host/Origin validation + Bearer Token authentication on every endpoint
    except /health and /ready.

    1. Host/Origin are checked first, with the SDK's own validator and the same
       allowlists the mcp app uses (the SDK check stays enabled behind us as
       defence in depth). Doing it here, before the request reaches the mcp
       app, avoids the SDK's SSE transport logging a "Request validation
       failed" traceback for every rejected request. It runs before auth so
       DNS-rebinding attempts are dropped without any token comparison work.
    2. The Authorization header must carry the user token or (if configured)
       the admin token. The caller's role is exposed to the tools as
       request.state.mcp_role ("admin" | "user").
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        admin_token: str | None = None,
        user_role: str = "user",
        security_settings: TransportSecuritySettings | None = None,
    ) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()
        self._expected_admin = f"Bearer {admin_token}".encode() if admin_token else None
        self._user_role = user_role
        self._security: TransportSecurityMiddleware | None = None
        if security_settings is not None:
            from mcp.server.transport_security import TransportSecurityMiddleware

            self._security = TransportSecurityMiddleware(security_settings)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type == "websocket":
            # No MCP transport here uses websockets; never reach the app.
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope_type not in ("http", "lifespan"):
            return
        if scope_type == "http" and scope.get("path", "") not in PUBLIC_PATHS:
            if self._security is not None:
                error = await self._security.validate_request(HTTPConnection(scope), is_post=False)
                if error is not None:
                    await error(scope, receive, send)
                    return

            provided = b""
            for key, value in scope.get("headers", []):
                if key.lower() == b"authorization":
                    provided = value
                    break

            # Compare against both tokens unconditionally (no short-circuit),
            # so timing does not reveal which one matched.
            is_user = hmac.compare_digest(provided, self._expected)
            is_admin = False
            if self._expected_admin is not None:
                is_admin = hmac.compare_digest(provided, self._expected_admin)

            if not (is_user or is_admin):
                response = JSONResponse(
                    {
                        "error": "Unauthorized",
                        "message": "Missing or invalid Bearer token in Authorization header.",
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return

            role = "admin" if is_admin else self._user_role
            # Fresh per-request copy: never mutate a state dict that might be
            # shared with other requests. Starlette's Request.state reads it.
            scope = {**scope, "state": {**scope.get("state", {}), "mcp_role": role}}

        await self.app(scope, receive, send)


# ── App factory ──────────────────────────────────────────────
def create_remote_app(
    token: str | None = None,
    transport: str | None = None,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    admin_token: str | None = None,
) -> Starlette:
    """Build the authenticated Starlette application.

    Arguments default to the corresponding environment variables. Raises
    ConfigError when a token is missing/too short, the admin token equals the
    auth token, the admin configuration is contradictory, the transport is
    invalid, the Google key is unusable or DATABASE_URL is unset. Compatible with
    `uvicorn --factory dialogflow_mcp.remote_server:create_remote_app`.
    """
    token = _resolve_token(token)
    transport = _resolve_transport(transport)
    admin_token = _resolve_admin_token(admin_token, token)
    _validate_gcp_credentials()
    _require_database_url()

    if admin_token is not None:
        # Must happen before server_es is imported (it reads the flag then).
        _enable_admin_tools_for_admin_token()
        user_role = "user"
    else:
        # No separate admin credential: the shared token is the only role, so
        # admin tools (if MCP_ADMIN_TOOLS is on) are usable by every caller.
        if _ADMIN_TOOLS_FORCED_ON:
            raise ConfigError(
                "create_remote_app() already ran in this process with MCP_ADMIN_TOKEN; "
                "start the remote server in a fresh process."
            )
        user_role = "admin" if admin_tools_enabled() else "user"

    from mcp.server.transport_security import TransportSecuritySettings

    # Deferred import: MCP_ADMIN_TOOLS must be final before this point.
    from dialogflow_mcp import server_es
    from dialogflow_mcp.server_es import mcp

    # Fail closed: in remote mode an admin tool refuses any call that does not
    # carry a request tagged "admin" by the auth middleware (ADR 0001).
    server_es.require_request_role()
    # Importing server_es constructs FastMCP, which puts a Rich handler on the
    # root logger; it wraps long lines. A plain stderr handler keeps one record
    # on one line, so `admin_tool_denied ...` is greppable in hosted logs
    # (ADR 0008). Done here, not in main(): `uvicorn --factory` skips main().
    logging.basicConfig(
        force=True,
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    hosts = build_allowed_hosts(allowed_hosts)
    origins = build_allowed_origins(allowed_origins)

    # FastMCP defaults to host=127.0.0.1, which auto-enables DNS rebinding
    # protection with localhost-only hosts (=> "421 Invalid Host header" behind
    # a proxy/domain). Keep protection ON but widen the allowlist explicitly.
    # Must be set before sse_app()/streamable_http_app() is first called.
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )
    mcp.settings.transport_security = security

    # For streamable-http, the returned app's lifespan runs the
    # StreamableHTTPSessionManager; we mutate this same app (add_route /
    # add_middleware) so it is preserved.
    app = mcp.streamable_http_app() if transport == "streamable-http" else mcp.sse_app()

    async def health_check(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "healthy",
                "server": "dialogflow-food-ordering-mcp",
                "transport": transport,
            }
        )

    async def readiness_check(_request: Request) -> JSONResponse:
        return await readiness_response()

    app.add_route("/health", health_check, methods=["GET"])
    app.add_route("/ready", readiness_check, methods=["GET"])
    app.add_middleware(
        BearerAuthASGIMiddleware,
        token=token,
        admin_token=admin_token,
        user_role=user_role,
        security_settings=security,
    )

    app.state.mcp_transport = transport
    app.state.admin_token_configured = admin_token is not None
    app.state.allowed_hosts = hosts
    app.state.allowed_origins = origins
    return app


def main() -> None:
    """Entry point to launch the remote MCP server with Uvicorn."""
    import uvicorn

    try:
        app = create_remote_app()
    except ConfigError as exc:
        raise SystemExit(f"[ERROR] {exc}") from None

    transport = app.state.mcp_transport
    endpoint = "/mcp" if transport == "streamable-http" else "/sse"

    print("=" * 70)
    print("[SERVER]  Dialogflow + PostgreSQL Remote MCP Server")
    print(f"[HOST]    Listening on:  http://{HOST}:{PORT}")
    print(f"[MCP]     Transport:     {transport}  (endpoint {endpoint})")
    print("[AUTH]    Bearer token:  configured (value not shown)")
    print(f"[HOSTS]   Allowed Host headers:   {', '.join(app.state.allowed_hosts)}")
    print(f"[ORIGINS] Allowed Origin headers: {', '.join(app.state.allowed_origins)}")
    if app.state.admin_token_configured:
        print("[ADMIN]   Admin tools:   ENABLED for the admin token only (MCP_ADMIN_TOKEN)")
    else:
        print(
            f"[ADMIN]   Admin tools:   {'ENABLED' if admin_tools_enabled() else 'disabled'}"
            " (MCP_ADMIN_TOOLS)"
        )
    shared_token_warning = _shared_token_admin_warning(app)
    if shared_token_warning:
        print(f"[WARN]    {shared_token_warning}")
    print("[WARN]    Serve this only behind HTTPS (a TLS-terminating reverse proxy).")
    print("          Over plain HTTP the Bearer token travels in cleartext.")
    if not _split_env_list("MCP_ALLOWED_HOSTS"):
        print("[WARN]    MCP_ALLOWED_HOSTS is empty: only localhost Host headers are")
        print("          accepted; requests via a public hostname will get 421.")
    for problem in missing_runtime_config():
        print(f"[WARN]    {problem}")
    print("=" * 70)

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
