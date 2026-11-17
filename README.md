# Dialogflow MCP Server

[![CI](https://github.com/Nyiko-Shabangu/dialogflow-mcp-server/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Nyiko-Shabangu/dialogflow-mcp-server/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Nyiko-Shabangu/dialogflow-mcp-server)](https://github.com/Nyiko-Shabangu/dialogflow-mcp-server/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Docker image on GHCR](https://img.shields.io/badge/ghcr.io-dialogflow--mcp--server-2496ED?logo=docker&logoColor=white)](https://github.com/Nyiko-Shabangu/dialogflow-mcp-server/pkgs/container/dialogflow-mcp-server)

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that turns a Google **Dialogflow ES** food-ordering agent and its **PostgreSQL** backend into tools any AI client can call. Claude, Cursor or your own LLM app can understand a customer's message through Dialogflow, look up the menu, place an atomic order and track it, all through standard MCP tool calls. The remote server runs over Streamable HTTP with Bearer-token auth and a separate admin role, and keeps every database and Google credential on the server. The Dialogflow agent is versioned as code and measured with an NLU eval harness.

**NLU results (intent accuracy):** dev set **73.6% → 98.4%**; held-out set **87.0%**. The dev set (182 utterances) was used to find and fix the failures, so 98.4% is not a generalisation estimate; the held-out set (54 utterances, written separately from the dev set and never tuned on) is. The baseline agent had a broken Welcome intent (its training phrases were the bot's own greetings), so part of the gain is repair rather than tuning. Still failing: the dev-set happy path "2 bunny chows and a milk tart" (and 2 more of 16 dev conversations), and 3 of 5 held-out conversations, mostly because "a"/"an" is not read as a quantity of 1. Details in [NLU evals](#nlu-evals).

The demo domain is a South African food-ordering chatbot (Bunny Chow, Kota, Bobotie, Malva Pudding, ...), priced in rand. It grew out of my earlier [NLP-Food](https://github.com/Nyiko-Shabangu/NLP-Food) project, a Dialogflow food-ordering chatbot with a FastAPI webhook and MySQL. This project replaces the webhook with MCP tools and PostgreSQL, and adds the remote server, its security model, the eval harness and the deployment.

## Features

- **8 MCP tools** over Dialogflow ES and Postgres: intent detection with conversation logging, menu lookup, atomic order placement, order tracking, and three admin tools. A second, minimal server wraps a **Dialogflow CX** agent (optional `cx` extra).
- **Three transports**: stdio for local clients; Streamable HTTP (`/mcp`) or legacy SSE (`/sse`) for the remote server.
- **Two roles, fail closed**: a required user token and an optional admin token, compared in constant time. Admin tools can only be registered through an `admin_tool` decorator that checks the role on every call; in remote mode a call without a request tagged `admin` is refused. `track_order` returns an allowlist of order fields to non-admin callers, so customer name, phone and address stay hidden.
- **DNS-rebinding protection**: Host and Origin allowlists on every endpoint except the health probes.
- **Atomic, bounded orders**: item lookup, duplicate merging, the order row and all line items in one transaction, with caps on entries, distinct items and quantities.
- **Clean errors**: inputs are validated before they reach Dialogflow or Postgres, and any database error reaches the client only as a generic "temporarily unavailable" message.
- **Non-blocking**: tools are `async` and run blocking Postgres/Dialogflow calls in worker threads, so a slow call never stalls `/health` or other clients.
- **Liveness and readiness**: `/health` and `/ready` (database `SELECT 1` plus required settings).
- **Agent as code**: the Dialogflow agent lives in [`dialogflow/agent/`](dialogflow/agent) with an export/restore script; the pre-fix agent is kept in [`evals/agents/baseline-2026-11-17/`](evals/agents/baseline-2026-11-17) so the baseline can be reproduced.
- **NLU evals**: dev and held-out golden sets, confusion matrix, slot accuracy, per-tag results, latency, a training-phrase overlap check and a baseline regression gate in [`evals/`](evals).
- **Production packaging**: multi-stage non-root Docker image, Railway configuration as code, Neon Postgres, CI (ruff, mypy, pytest on Python 3.10 and 3.14 with a real Postgres, Docker smoke test with an authenticated MCP `initialize`), and tag-driven releases to GHCR with third-party actions pinned to commit SHAs.

## Architecture

```mermaid
flowchart LR
    subgraph Clients
        C1[Claude Code / Claude Desktop]
        C2[Cursor / custom LLM app]
    end
    subgraph Server["dialogflow-mcp-server (Python, Starlette + FastMCP)"]
        A[Bearer auth + Host/Origin checks]
        T[MCP tools<br/>async, blocking work in worker threads]
        H["/health, /ready"]
    end
    DF[Google Dialogflow ES agent<br/>intents + food_Item entity]
    PG[(PostgreSQL<br/>menu, orders, conversation logs)]
    P[Platform probes] -- "no token" --> H

    C1 -- "stdio or HTTPS /mcp" --> A
    C2 -- "HTTPS /mcp or /sse" --> A
    A --> T
    T -- "gRPC detect_intent" --> DF
    T -- "psycopg 3 pool" --> PG
```

A client sends the customer's text to `detect_intent`. Dialogflow returns the intent (`new.order`, `order.add`, `order_remove`, `order.complete`, `track.order`) and the extracted dishes and quantities, and the server logs the turn to Postgres. The client then calls the database tools to act on it: `get_menu`, `complete_order`, `track_order`. Webhook fulfilment in Dialogflow is off on purpose; the MCP client does the work through tools. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the components, security model, data model and deployment topology.

## Quick start

Prerequisites: Python 3.10+, [`uv`](https://docs.astral.sh/uv/), Docker, and a Google Cloud project with the Dialogflow API enabled and an ES agent (you can restore the one in [`dialogflow/agent/`](dialogflow/agent), see [Dialogflow agent](#dialogflow-agent)). `mcp dev` (the MCP Inspector) also needs Node.js, because it starts the Inspector with `npx`.

### 1. Local, over stdio

```bash
git clone https://github.com/Nyiko-Shabangu/dialogflow-mcp-server.git
cd dialogflow-mcp-server
uv sync
docker compose up -d                      # Postgres 17 on 127.0.0.1:5433: schema, menu, demo orders
gcloud auth application-default login     # or set GOOGLE_APPLICATION_CREDENTIALS

# Try it in the MCP Inspector
GCP_PROJECT=your-gcp-project-id uv run mcp dev src/dialogflow_mcp/server_es.py
```

The stdio servers fall back to the docker compose database (`postgresql://mcp:mcp_local_dev@127.0.0.1:5433/dialogflow_mcp`) when `DATABASE_URL` is unset; the remote server never does.

Add it to **Claude Code**:

```bash
claude mcp add food-ordering \
  -e GCP_PROJECT=your-gcp-project-id \
  -e DATABASE_URL=postgresql://mcp:mcp_local_dev@127.0.0.1:5433/dialogflow_mcp \
  -- uv run --directory /path/to/dialogflow-mcp-server dialogflow-mcp-es
```

or to **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "food-ordering": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/dialogflow-mcp-server", "dialogflow-mcp-es"],
      "env": {
        "GCP_PROJECT": "your-gcp-project-id",
        "DATABASE_URL": "postgresql://mcp:mcp_local_dev@127.0.0.1:5433/dialogflow_mcp"
      }
    }
  }
}
```

The Python servers do not read `.env` themselves. Export variables in your shell, put them in the client's `env` block, or use `uv run --env-file .env ...`.

For a **Dialogflow CX** agent: `GCP_PROJECT=... DF_AGENT_ID=... uv run dialogflow-mcp` (`uv sync` installs the CX client for development; with pip, install `dialogflow-mcp-server[cx]`). The CX server is a minimal example: it validates its configuration but not its inputs, and passes Dialogflow errors through.

### 2. Run with Docker

The remote server is published as a container image on GHCR:

```bash
docker run --rm -p 8000:8000 \
  -e MCP_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  -e DATABASE_URL="postgresql://mcp:mcp_local_dev@host.docker.internal:5433/dialogflow_mcp" \
  -e GCP_PROJECT=your-gcp-project-id \
  -e GOOGLE_APPLICATION_CREDENTIALS_JSON="$(cat /path/to/service-account-key.json)" \
  ghcr.io/nyiko-shabangu/dialogflow-mcp-server:latest

curl http://127.0.0.1:8000/health    # 200
curl http://127.0.0.1:8000/ready     # 200 once the database answers and settings are complete
```

On Linux, `host.docker.internal` does not resolve by default, and the compose database listens on the host's loopback only. Use `--network host` (drop `-p`) with `DATABASE_URL=...@127.0.0.1:5433/...`, or point `DATABASE_URL` at a database the container can reach.

Or build it yourself: `docker build -t dialogflow-mcp-server .`. The image runs as a non-root user, binds `0.0.0.0:$PORT` (default 8000) and has a `HEALTHCHECK`.

### 3. Remote, over HTTPS

```bash
MCP_AUTH_TOKEN="<user-token>" \
MCP_ADMIN_TOKEN="<admin-token>" \
MCP_ALLOWED_HOSTS="mcp.example.com" \
GCP_PROJECT=your-gcp-project-id \
DATABASE_URL="postgresql://..." \
uv run dialogflow-mcp-remote          # or: uvicorn --factory dialogflow_mcp.remote_server:create_remote_app
```

The server speaks plain HTTP, so put it behind TLS (a PaaS such as Railway, or Caddy/nginx). Then connect a client:

```bash
claude mcp add --transport http food-ordering-remote https://mcp.example.com/mcp \
  --header "Authorization: Bearer <user-token>"
```

Claude Desktop can use a custom connector (URL `https://mcp.example.com/mcp`, header `Authorization: Bearer <token>`), or the [`mcp-remote`](https://github.com/geelen/mcp-remote) bridge for servers only your machine can reach.

## Configuration

See [`.env.example`](.env.example) for a commented template.

| Variable | Required | Default | Description |
|---|---|---|---|
| `GCP_PROJECT` | yes | | GCP project that hosts the Dialogflow agent |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | | ADC | Service-account key **content** (JSON), for hosts without file mounts. Wins over `GOOGLE_APPLICATION_CREDENTIALS`. The remote server refuses to start if it is set but invalid |
| `DF_LOCATION` | | `global` | Dialogflow region, e.g. `europe-west2` |
| `DF_ENVIRONMENT` | | draft | Dialogflow ES environment name |
| `DF_AGENT_ID` | CX only | | Dialogflow CX agent id |
| `DATABASE_URL` | remote | stdio only: `postgresql://mcp:mcp_local_dev@127.0.0.1:5433/dialogflow_mcp` | Postgres connection string. The remote server refuses to start without it |
| `DB_POOL_MAX` / `DB_POOL_MIN` / `DB_POOL_TIMEOUT` | | `5` / `0` / `10` s | psycopg pool size and wait timeout |
| `DB_POOL_CHECK` | | `on` | Ping each pooled connection when it is handed out. Costs one round trip per checkout; keeps the first query after an idle period (Neon suspends, proxies drop connections) from failing. `off` saves the round trip on a nearby, stable database |
| `MCP_AUTH_TOKEN` | remote | | User Bearer token, at least 16 characters; the remote server will not start without it |
| `MCP_ADMIN_TOKEN` | | | Optional admin Bearer token, at least 16 characters and different from `MCP_AUTH_TOKEN`. Turns the admin tools on for this token only |
| `MCP_ADMIN_TOOLS` | | `on` (stdio), `off` (remote) | Register the admin tools. A blank value counts as unset. Explicitly falsy (`off`, `0`, `false`, `no`) together with `MCP_ADMIN_TOKEN` is a startup error. In remote mode, values other than `1`/`true`/`yes`/`on` mean off |
| `MCP_TRANSPORT` | | `streamable-http` | `streamable-http` (`/mcp`) or `sse` (`/sse`) |
| `MCP_ALLOWED_HOSTS` | public host | localhost only | Extra allowed `Host` headers, comma-separated. Without your public hostname, requests get `421` |
| `MCP_ALLOWED_ORIGINS` | | localhost only | Extra allowed `Origin` headers for browser clients (else `403`) |
| `HOST` / `PORT` | | `0.0.0.0` / `8000` | Bind address and port of the remote server |
| `POSTGRES_PORT` / `POSTGRES_PASSWORD` | | `5433` / `mcp_local_dev` | docker compose only |

## Tools

| Tool | Role | What it does |
|---|---|---|
| `detect_intent` | user | Sends the customer's text to Dialogflow ES; returns reply, intent, confidence, parameters (`food_Item`, `number`) and contexts; logs the turn to Postgres |
| `get_menu` | user | Available menu items with ZAR prices, optionally filtered by category |
| `get_food_item_details` | user | One item by exact or unambiguous partial name |
| `complete_order` | user | Creates an order and its line items in one transaction; 1-50 per item, at most 20 distinct items |
| `track_order` | user | Status, items and total of an order. Customer name, phone and address only for admins |
| `update_order_status` | admin | Sets the status to one of `in progress`, `preparing`, `out for delivery`, `delivered`, `cancelled`. Any allowed value can be set at any time; there are no transition rules |
| `get_recent_orders` | admin | Latest orders, optionally by status (`limit` 1-100) |
| `get_conversation_logs` | admin | Logged turns, optionally for one session (`limit` 1-100) |

The CX server (`dialogflow-mcp`) exposes a single `detect_intent` tool that returns replies, intent, confidence, current page and parameters.

## Security model

- **Secrets stay on the server.** Clients hold only a Bearer token. `DATABASE_URL` and the Google key live in the server's environment.
- **Fail-closed startup.** The remote server refuses to start without `MCP_AUTH_TOKEN` or `DATABASE_URL`, with a token shorter than 16 characters, with an admin token equal to the user token, with contradictory admin settings, or with an unusable `GOOGLE_APPLICATION_CREDENTIALS_JSON`. Error messages never contain token values.
- **Two roles, checked inside the tools.** `MCP_AUTH_TOKEN` callers may use the customer tools. With `MCP_ADMIN_TOKEN` set, only that token may run the admin tools; other callers get a refusal and the server logs one line, `admin_tool_denied tool=<name> role=<role>`. In remote mode a call that does not carry a request tagged `admin` is refused, so the check does not depend on the SDK attaching the request ([ADR 0001](docs/adr/0001-fail-closed-admin-check.md)). A test fails if a new tool is neither declared public nor registered through `@admin_tool`. Both tokens are compared with `hmac.compare_digest`, always both, so timing does not reveal which matched. The admin tools stay visible in `tools/list` for user-token clients; they just refuse to run ([ADR 0004](docs/adr/0004-unfiltered-tools-list.md)).
- **PII minimisation.** Order ids are sequential, so `track_order` returns only an allowlist of fields (`order_id`, `total_price`, `order_status`, `created_at`, `updated_at`, plus the line items) to non-admin callers ([ADR 0002](docs/adr/0002-pii-allowlist.md)). HTTP tests check this with both tokens on both transports.
- **DNS-rebinding protection.** Host and Origin are checked against allowlists before the token on every endpoint except `/health` and `/ready`. WebSocket connections are closed.
- **Input validation and clean errors.** Session ids, text length, language codes, order ids, quantities and field lengths are validated before they reach Dialogflow or Postgres. SQL is parameterised, and `LIKE` wildcards in user input are escaped. Dialogflow and database error details are not echoed to clients; database errors are logged as `database_error tool=<name> error=<class> sqlstate=<code>`.
- **Least privilege.** The server's Google identity needs only *Dialogflow API Client*. Exporting or restoring the agent needs *Dialogflow API Admin*, which the server does not have.
- **Limits.** Tokens are shared secrets, not per-user identities: there is no OAuth, no per-user audit trail and no rate limiting in the app. MCP sessions are not yet bound to the token that opened them ([ADR 0007](docs/adr/0007-session-token-binding.md), proposed). TLS is the platform's job. In stdio mode the process trusts its local user, so admin tools are allowed. See [Roadmap](#roadmap) and [SECURITY.md](SECURITY.md).

## Testing

```bash
uv sync
uv run pytest -q          # unit, remote-server and eval tests; DB tests are skipped
uv run ruff check && uv run ruff format --check && uv run mypy
```

The remote-server tests serve the real ASGI app with uvicorn in a subprocess and drive it over HTTP: tokens, roles, `track_order` field filtering per token, Host/Origin checks, `/health` and `/ready`, startup configuration errors, and a regression test that a slow tool call does not block `/health` or a second client.

The PostgreSQL integration tests run when `TEST_DATABASE_URL` is set. **They drop and recreate the `public` schema before every test**, so use a throwaway database:

```bash
docker run -d --name df-mcp-pytest -e POSTGRES_DB=dialogflow_mcp_test \
  -e POSTGRES_USER=mcp -e POSTGRES_PASSWORD=x -p 55434:5432 postgres:17-alpine
TEST_DATABASE_URL=postgresql://mcp:x@localhost:55434/dialogflow_mcp_test uv run pytest -q
docker rm -f df-mcp-pytest
```

`tests/conftest.py` refuses (an error, not a skip) a `TEST_DATABASE_URL` whose database name does not end in `_test`; whose host is missing or not `localhost`/`127.0.0.1`/`::1`; whose port is missing, 5433 (the compose default) or equal to `POSTGRES_PORT`; that names the same database as `DATABASE_URL`; or that uses `hostaddr`/`service`. With `REQUIRE_DB_TESTS=1` (set in CI) a missing `TEST_DATABASE_URL` fails the run. The full suite has 480 tests: 400 run without a database and 80 need one.

### NLU evals

[`evals/`](evals) measures how well the Dialogflow agent understands customers, independent of the MCP server.

| Run | Set | Intent accuracy | Macro F1 | Case pass rate | `number` slot accuracy | Conversations |
|---|---|---|---|---|---|---|
| [Before](evals/reports/baseline-2026-11-17/report.md) (production, pre-fix agent) | dev, 182 cases | 73.6% | 0.62 | 69.2% | 81.8% | 9/16 |
| [After](evals/reports/prod-after-fixes-2026-11-17/report.md) (production, fixed agent) | dev, 182 cases | **98.4%** | 0.98 | 95.6% | 93.2% | 13/16 |
| [Re-run on staging](evals/reports/staging-dev-2026-11-17/report.md) (same agent) | dev, 182 cases | 98.4% | 0.98 | 95.6% | 93.2% | 13/16 |
| [Held-out](evals/reports/staging-heldout-2026-11-17/report.md) (same agent, run once) | held-out, 54 cases | **87.0%** | 0.85 | 77.8% | 68.0% | 2/5 |

How to read it:

- **The dev set guided the fixes.** It is 182 labelled single-turn utterances and 16 conversations covering slang, typos, multi-item orders, "a/an" quantities, off-menu dishes and order ids. 11 of its utterances are, after normalising case and punctuation, also training phrases of the fixed agent (some were added as fixes). They are labelled `verbatim_in_training`, and every report also gives the metrics without them: **76.6% → 98.2%** intent accuracy on the other 171 cases.
- **The held-out set did not.** 54 single-turn utterances and 5 conversations in [`evals/golden/heldout.yaml`](evals/golden/heldout.yaml), written for this purpose by a writer who saw only the menu, the intent names and the label rules, not the training phrases or the dev set. The writer had no access to the repository, but its brief was written by someone who had seen the training phrases and the dev set, and it included a few style hints, some local dish nicknames and one example utterance, so the set is independent of the dev set but not perfectly blind. Every candidate with a near match to a training phrase or a dev-set utterance was dropped mechanically before the run (16 of 70; [`evals/leakage.py`](evals/leakage.py)), and the agent was not changed afterwards. 87.0% is the number to expect on new phrasing; it is from one run of 54 utterances, so treat it as a rough estimate.
- **Part of the gain is repair.** In the baseline agent the Welcome intent's training phrases were the bot's own greeting texts, so "hi" fell through to the fallback (Welcome recall 0.00), and "a kota" was classified as a removal.
- **Known gaps.** "a"/"an" is not extracted as a quantity of 1 in multi-item messages, which fails the dev happy path ("2 bunny chows and a milk tart") and 2 of the 3 failing held-out conversations; a bare "2" after a quantity prompt and "dats it" fail in the dev conversations; on the held-out set, some slang dish names ("sphatlho"), "n" for "and" in removals, and tracking phrasings without the word "track" are missed.

The baseline agent is committed as [`evals/agents/baseline-2026-11-17/`](evals/agents/baseline-2026-11-17), so the 73.6% can be reproduced on a staging project (restore REPLACES that project's agent, and ES needs a minute or two to train):

```bash
uv run python scripts/dialogflow_agent.py restore --project <staging-project> --confirm <staging-project> --dir evals/agents/baseline-2026-11-17
uv run python evals/run_nlu.py --project <staging-project>                        # dev set, ~243 calls, ~2.5 min
# restore dialogflow/agent/ afterwards, then the held-out set:
uv run python evals/run_nlu.py --project <staging-project> --cases evals/golden/heldout.yaml --conversations evals/golden/heldout.yaml
```

`--dry-run` prints the call plan without calling Dialogflow, and `--project-label` keeps the project id out of a report you intend to commit. [`.github/workflows/evals.yml`](.github/workflows/evals.yml) runs the dev set against a staging project on demand and gates on a drop against the committed baseline.

### CI

[`ci.yml`](.github/workflows/ci.yml) runs on every push and pull request: ruff (lint and format), mypy, the golden-set overlap check, pytest on Python 3.10 and 3.14 against a Postgres 17 service container (the DB tests are required there), a package build, and a Docker build with a smoke test (`/health` 200, `/ready` 503 without a database, `POST /mcp` 401 without a token and 200 for an authenticated MCP `initialize`). [`release.yml`](.github/workflows/release.yml) runs on a `v*.*.*` tag: it checks that the tag matches the version in `pyproject.toml`, creates the GitHub Release from the matching CHANGELOG section, and only then publishes the GHCR image. Third-party actions are pinned to commit SHAs.

## Deployment (example: Railway + Neon)

The reference deployment runs the container on [Railway](https://railway.com) and the database on [Neon](https://neon.tech) serverless Postgres; both have free tiers that are enough for a demo. Any container host with TLS works the same way.

1. **Database.** Create a Neon project and run the schema once: `psql "<neon-connection-string>" -f db/init.sql` (tables and the menu; idempotent). For a demo, also run `db/seed_demo.sql`, which adds orders 1001-1004 with fictional customers; skip it for real use. Existing databases are upgraded with the files in [`db/migrations/`](db/migrations), in order.
2. **Service.** Create a Railway service from this repo. Railway builds the `Dockerfile`; build, health check (`/ready`) and restart settings are kept as code in [`.railway/railway.ts`](.railway/railway.ts) (`npm install --prefix .railway`, then `railway config plan` / `railway config apply`).
3. **Variables.** Set `MCP_AUTH_TOKEN` (and optionally `MCP_ADMIN_TOKEN`), `MCP_ALLOWED_HOSTS=your-service.up.railway.app`, `DATABASE_URL` (Neon's direct connection string; the server keeps its own small pool), `GCP_PROJECT` and `GOOGLE_APPLICATION_CREDENTIALS_JSON` (the whole key file content; Railway has no file mounts). Do not set `HOST`.
4. **Check.** `curl https://your-service.up.railway.app/health` returns 200, `/ready` returns 200, and `POST /mcp` without a token returns 401.

Free-tier notes: Neon suspends idle computes, so the first query after a pause is slower; the pool waits up to `DB_POOL_TIMEOUT` (10 s) for a connection. Dialogflow ES Trial edition text requests are free but rate-limited per minute. Keep the eval workflow manual.

## Dialogflow agent

[`dialogflow/agent/`](dialogflow/agent) holds the "SA Food Ordering" ES agent: intents and training phrases, the `food_Item` entity (exactly the nine menu dishes, matching `food_items.name`), and contexts.

```bash
# pull an agent into dialogflow/agent/ (review the git diff afterwards)
uv run python scripts/dialogflow_agent.py export --project your-gcp-project-id --gcloud
# push dialogflow/agent/ into a project; this REPLACES that project's whole agent
uv run python scripts/dialogflow_agent.py restore --project your-gcp-project-id --confirm your-gcp-project-id --gcloud
```

Export blanks the webhook URL and credentials and the legacy V1 key. Fulfilment is off, so every reply comes from static responses and the MCP client does the real work. Note that the `number` list can be shorter than the `food_Item` list ("a kota" has no number); treat a missing quantity as 1.

## Project structure

```text
src/dialogflow_mcp/
  server_es.py        Dialogflow ES + PostgreSQL tools, admin gate (stdio entry point: dialogflow-mcp-es)
  server.py           Dialogflow CX detect_intent (stdio entry point: dialogflow-mcp)
  remote_server.py    Authenticated Starlette app: /mcp or /sse, /health, /ready (dialogflow-mcp-remote)
  database.py         psycopg 3 connection pool and query helpers
  gcp_auth.py         Google credentials from GOOGLE_APPLICATION_CREDENTIALS_JSON or ADC
db/init.sql           Schema and menu; db/seed_demo.sql demo orders; db/migrations/ for upgrades
dialogflow/agent/     The Dialogflow ES agent as code
scripts/              Agent export/restore
evals/                NLU golden sets (dev, held-out), runner, metrics, overlap check, reports, baseline agent
tests/                Unit, remote-server (real uvicorn), DB integration and eval tests
docs/                 ARCHITECTURE.md and architecture decision records (docs/adr/)
.railway/             Railway configuration as code
.github/workflows/    CI, release (GitHub Release + GHCR image) and manual NLU eval
```

## Design decisions

The main decisions are recorded as ADRs in [`docs/adr/`](docs/adr). "Accepted" means built and tested; "Proposed" means designed but not built.

| ADR | Decision | Status |
|---|---|---|
| [0001](docs/adr/0001-fail-closed-admin-check.md) | Fail-closed admin check in remote mode, admin tools registered by construction | Accepted |
| [0002](docs/adr/0002-pii-allowlist.md) | Redact `track_order` with an allowlist for non-admin callers | Accepted |
| [0003](docs/adr/0003-token-comparison.md) | Constant-time comparison of the Authorization header against both tokens | Accepted |
| [0004](docs/adr/0004-unfiltered-tools-list.md) | Keep `tools/list` unfiltered and enforce the role inside the tools | Accepted |
| [0005](docs/adr/0005-admin-tools-env-precedence.md) | `MCP_ADMIN_TOOLS` / `MCP_ADMIN_TOKEN` precedence; a blank value means unset | Accepted |
| [0006](docs/adr/0006-ci-and-db-tests.md) | CI on every push, database tests required in CI and guarded against the wrong database | Accepted |
| [0007](docs/adr/0007-session-token-binding.md) | Bind each MCP session to the credential that opened it | Proposed |
| [0008](docs/adr/0008-plain-log-handler-remote.md) | Plain single-line log handler in remote mode | Accepted |

## Roadmap

Not built yet, in rough priority order:

- **Session-to-token binding** ([ADR 0007](docs/adr/0007-session-token-binding.md)): tie each MCP session to the credential that opened it, so a leaked session id cannot be used with the other token.
- **Better quantity handling in the agent**: read "a"/"an" as 1 in multi-item orders, and handle a bare number after a quantity prompt. Measure on the held-out set, then write a fresh held-out set, because this one will have been looked at.
- **Per-user identity**: OAuth 2.1 (the MCP authorization spec) instead of shared tokens, with a per-user audit trail and token rotation without a redeploy.
- **Rate limiting** in the app, or a gateway in front of it.
- **Keyless CI auth for the eval workflow**: Workload Identity Federation instead of a service-account key in a GitHub secret.
- **Supply chain**: pin the Docker base images by digest.
- **Configuration object**: build the FastMCP server from a settings object instead of reading the environment at import time, which would let tests build several apps in one process.
- **Scale-out**: MCP session state lives in one process today, so more than one replica needs sticky routing.
- **CX server**: the same validation and error handling as the ES server, or more tools.

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: see [SECURITY.md](SECURITY.md).

This project was developed in a private repository; its history was squashed into a single commit for the public release.

## License

[MIT](LICENSE) © 2026 Nyiko Shabangu
