# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-17

First public release.

### Added

- **MCP server for a Dialogflow ES food-ordering agent with PostgreSQL.** Eight tools:
  `detect_intent`, `get_menu`, `get_food_item_details`, `track_order` and `complete_order`
  for customers; `update_order_status`, `get_recent_orders` and `get_conversation_logs`
  for admins. A separate stdio server wraps a Dialogflow CX agent (`detect_intent`),
  installed with the optional `cx` extra.
- **Three transports.** stdio for local clients, and an authenticated remote server
  over Streamable HTTP (`/mcp`, default) or legacy SSE (`/sse`).
- **Auth model for the remote server.** A required Bearer token (`MCP_AUTH_TOKEN`)
  and an optional, separate admin token (`MCP_ADMIN_TOKEN`), compared in constant time.
  Admin tools are registered only through an `admin_tool` decorator that checks the
  caller's role on every call and fails closed in remote mode (a call without a request
  tagged `admin` is refused), with one `admin_tool_denied` log line per refusal. A tool
  inventory test fails for any tool that is neither public nor an admin tool.
  `track_order` returns an allowlist of order fields to non-admin callers, so customer
  name, phone and address stay hidden. Host and Origin headers are validated against an
  allowlist (DNS-rebinding protection). Database and Google credentials stay on the server.
- **Fail-closed startup.** The remote server refuses to start without `MCP_AUTH_TOKEN` or
  `DATABASE_URL`, with weak or equal tokens, contradictory admin settings, a second app
  built without the admin token in the same process, or an unusable Google key. A blank
  `MCP_ADMIN_TOOLS` counts as unset.
- **Atomic, bounded orders.** `complete_order` resolves items, merges duplicates and
  inserts the order and all line items in one transaction, with caps on entries,
  distinct items and quantity per item. Inputs are validated before they reach
  Dialogflow or Postgres, and any database error reaches clients only as a generic
  "temporarily unavailable" message (logged with its class and SQLSTATE).
- **Non-blocking tools.** Tools are `async` and run their blocking Postgres and
  Dialogflow calls in worker threads, so one slow call does not stall `/health` or
  other clients.
- **Health and readiness probes.** `/health` (liveness) and `/ready` (database
  `SELECT 1` plus required configuration), both unauthenticated.
- **Plain, one-line logs in remote mode**, so security events can be found with a
  single search.
- **Deployment example.** Multi-stage, non-root Docker image; Railway configuration
  as code (`.railway/railway.ts`) with `/ready` as the health check; Neon serverless
  Postgres; service-account key passed as an environment variable for hosts without
  file mounts. Schema and menu in `db/init.sql`, demo orders with fictional customers
  in `db/seed_demo.sql`, and a forward migration in `db/migrations/`.
- **Agent as code.** The Dialogflow ES agent (intents, training phrases, the
  `food_Item` entity) is versioned in `dialogflow/agent/`, with an export/restore
  script (`scripts/dialogflow_agent.py`) that blanks the webhook URL and credentials on
  export. The pre-fix agent is kept in `evals/agents/baseline-2026-11-17/`.
- **NLU eval harness** (`evals/`). A dev set of 182 single-turn utterances and 16
  conversations, a held-out set of 54 utterances and 5 conversations written separately and
  filtered against the training phrases and the dev set, intent accuracy, macro F1, a
  confusion matrix, slot accuracy, per-tag results, latency, metrics with and without
  dev cases that are verbatim training phrases, and a baseline regression gate.
  Training-phrase fixes raised dev-set intent accuracy from **73.6% to 98.4%**; the
  same agent scores **87.0%** on the held-out set.
- **Tests and CI.** Unit tests, remote-server tests that serve the real ASGI app over
  HTTP, PostgreSQL integration tests guarded against pointing at a real database, and
  eval tests. GitHub Actions runs ruff, mypy and pytest on Python 3.10 and 3.14 (the
  database tests are required), and smoke-tests the Docker image including an
  authenticated MCP `initialize`. A tag push checks the tag against the package version,
  publishes a GitHub Release and then the image to GHCR. Third-party actions are pinned
  to commit SHAs.

[Unreleased]: https://github.com/Nyiko-Shabangu/dialogflow-mcp-server/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Nyiko-Shabangu/dialogflow-mcp-server/releases/tag/v1.0.0
