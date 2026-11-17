# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-11-18

### Added

- **`menu.ask` intent** in the Dialogflow agent (`dialogflow/agent/`, synced with the
  production agent): availability, price and menu questions ("do you have bobotie",
  "how much is a bunny chow", "what's on the menu"), with an optional `food_Item` list.
  It answers with a static menu reply and never adds anything to the order, so these
  questions are no longer captured by `order.add`.
- **`order_line` support in `detect_intent`** (backward compatible). When the agent
  returns a composite `order_line` parameter (a list of `{food_Item, number}` pairs),
  the tool derives aligned flat `food_Item` and `number` lists from it (a missing number
  becomes 1, `"1"` and `2.0` become integers), returns `order_line` unchanged, and logs the
  derived parameters. Without `order_line` the parameters are returned exactly as before.
  The current agent does not return `order_line` yet; this prepares for an agent that
  does without breaking existing consumers.
- **30 dev-set cases** (`added_2026-11-17b`): mixed "a"/digit quantities, "a couple" /
  "a dozen" / "a single", "a quarter bunny", "vetcook", completion slang and 10 `menu.ask`
  cases. The dev set is now 212 single-turn cases.
- **Eval harness support for `order_line`**: when present, `food_Item`/`number` are scored
  from it with the server's rule, and the report adds a raw (dish, quantity) pair row.
- **v1.1.0 eval reports** against the production agent: `evals/reports/v1.1.0-dev/` and
  `evals/reports/v1.1.0-heldout/`. The earlier reports are kept.

### Changed

- **Agent training data**: 20 more `order.complete` phrases ("dats it", "no thats it",
  "that will be everything", ...), 12 more `order.add` phrases (off-menu mixes, quarter
  bunnies, measure words), and `food_Item` synonyms (vetcook, vetcooks, fetkoek; quarter,
  half and full bunny). The `order.add` schema is unchanged: `number` is optional with a
  default of 1 and may arrive as a float or the string `"1"`.
- **NLU results.** Dev set intent accuracy 99.5% (211/212); on the original 182 cases
  98.4% → 99.5%, case pass rate 95.6% → 96.7%, `number` accuracy 93.2% → 94.3%.
  Held-out set 87.0% → 88.9% (47/54 → 48/54; 86.8% → 88.7% without `ho-complete-01`).
  The held-out change is one utterance from one run, so it is about the same as before.
  Still wrong in the NLU layer: mixed quantities such as "a kota and 2 bunny chows"
  (`number` comes back as `[2]`). Consumers should use `order_line` when present and
  otherwise not pair `food_Item` and `number` by position.
- **conv-09 relabelled**: the quantity defaults to 1, so "I want bunny chow" adds one and
  the bot no longer asks "how many?"; a stray bare "2" must not be acted on (fallback).
  Dev conversations are 15/16, not like-for-like with v1.0.0's 13/16.
- **Regression-gate baseline** for the manual eval workflow is now
  `evals/reports/v1.1.0-dev/results.json` (the golden set changed).
- `detect_intent` tool description lists `menu.ask` and the optional `order_line`, and
  tells clients not to pair `food_Item` and `number` by position.
- 13 dev cases are now labelled `verbatim_in_training` (was 11; "thats all" and
  "thats it" became training phrases).

### Fixed

- Menu and price questions asked during an order ("is the kota available") were matched
  as `order.add` and could add a dish to the order; they now match `menu.ask`.
- "dats it" now completes the order, and "3 vetcooks" now adds Vetkoek.
- Held-out leakage check: `ho-complete-01` ("thats all thanks") became an
  `order.complete` training phrase in this release. The phrases were written in a
  repository that never contained the held-out set, and the case already passed before.
  The held-out file is not edited; the overlap is documented in
  `evals/leakage.py` (`HELDOUT_KNOWN_OVERLAP`), and any other near match still fails CI.

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

[Unreleased]: https://github.com/Nyiko-M-Shabangu/dialogflow-mcp-server/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/Nyiko-M-Shabangu/dialogflow-mcp-server/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Nyiko-M-Shabangu/dialogflow-mcp-server/releases/tag/v1.0.0
