# Contributing

Thanks for taking a look. Bug reports, questions and pull requests are welcome.

## Development setup

```bash
uv sync                    # Python 3.10+; installs the dev group (pytest, ruff, mypy, ...)
docker compose up -d       # local Postgres on 127.0.0.1:5433 with the menu and demo orders
uv run pytest -q           # unit, remote-server and eval tests (DB tests skipped)
uv run ruff check && uv run ruff format --check && uv run mypy
```

Run the database integration tests against a **separate, throwaway** Postgres, not the compose database: they drop and recreate the `public` schema. `tests/conftest.py` refuses a URL whose database name does not end in `_test`, that is not on localhost, or that uses port 5433 (the compose database).

```bash
docker run -d --name df-mcp-pytest -e POSTGRES_DB=dialogflow_mcp_test \
  -e POSTGRES_USER=mcp -e POSTGRES_PASSWORD=x -p 55434:5432 postgres:17-alpine
TEST_DATABASE_URL=postgresql://mcp:x@localhost:55434/dialogflow_mcp_test uv run pytest -q
docker rm -f df-mcp-pytest
```

## Pull requests

- Keep changes focused, and add or update tests with the code.
- `uv run pytest -q` must pass, including the DB tests (CI runs them on Python 3.10 and 3.14), and so must ruff and mypy.
- Tools must stay `async def` and run blocking work through `_run_db` / `_run_blocking` (see `tests/test_nonblocking.py`).
- A new tool is either public (`@mcp.tool()`; add it to `PUBLIC_TOOLS` in `tests/test_admin_gate.py` only if every user-token holder may see its output) or an admin tool (`@admin_tool`).
- Tool docstrings are the descriptions MCP clients see; change them deliberately.
- Changes to `dialogflow/agent/` should come with an NLU eval run against a staging copy of the agent (see [`evals/README.md`](evals/README.md)), and `uv run python evals/leakage.py --strict` must pass. Never add held-out utterances to the training phrases. Never restore into a production project from a branch.
- Never commit secrets: `.env`, service-account keys and any non-local connection string stay out of git. The only passwords in the repo are the local-development and CI defaults (`mcp_local_dev`, `ci_only_password`, `x`).
- Add a line to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md).

## Releases

Bump `version` in `pyproject.toml`, move the `Unreleased` notes to a new `## [x.y.z] - YYYY-MM-DD` section, then push a `vx.y.z` tag. The release workflow creates the GitHub Release from that section and publishes the image to GHCR.
