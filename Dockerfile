# Dockerfile — image for the remote (HTTP) MCP server: dialogflow-mcp-remote
#
# Build:  docker build -t dialogflow-mcp-server .
# Pull:   docker pull ghcr.io/nyiko-shabangu/dialogflow-mcp-server:latest
# Deploy: Railway builds this file automatically (see .railway/railway.ts and the
#         "Deploy to Railway" section of README.md). The container needs
#         MCP_AUTH_TOKEN, MCP_ALLOWED_HOSTS, DATABASE_URL, GCP_PROJECT and Google
#         credentials: GOOGLE_APPLICATION_CREDENTIALS_JSON (the key file's
#         content) or GOOGLE_APPLICATION_CREDENTIALS (path to a mounted key).

# ── Build stage: resolve the locked dependencies into /app/.venv ──────────
FROM ghcr.io/astral-sh/uv:0.12.8 AS uv

FROM python:3.14-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first (cached layer as long as pyproject.toml / uv.lock don't change).
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project

# Then the project itself, installed non-editable so the venv is self-contained.
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# ── Runtime stage: just Python + the venv, running as a non-root user ─────
FROM python:3.14-slim

LABEL org.opencontainers.image.title="dialogflow-mcp-server" \
      org.opencontainers.image.description="Remote MCP server exposing a Dialogflow ES food-ordering agent and PostgreSQL as tools" \
      org.opencontainers.image.source="https://github.com/Nyiko-Shabangu/dialogflow-mcp-server" \
      org.opencontainers.image.licenses="MIT"

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000

USER app
EXPOSE 8000

# /health (liveness) and /ready (readiness) are the unauthenticated endpoints.
# This liveness check is used by `docker run` / compose;
# Railway ignores HEALTHCHECK and probes the healthcheck from .railway/railway.ts instead.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"]

CMD ["dialogflow-mcp-remote"]
