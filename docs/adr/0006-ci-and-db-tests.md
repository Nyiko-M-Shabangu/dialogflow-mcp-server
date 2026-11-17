# ADR 0006: CI on every push, database tests required in CI and guarded against the wrong database

- Status: Accepted
- Date: 2026-11-17

## Context

`tests/test_db.py` drops and recreates the `public` schema. It is skipped unless `TEST_DATABASE_URL` is set. A developer may have a local Postgres with dev data, or a production `DATABASE_URL` in the shell, or a tunnel to a hosted database on `localhost:5432`. None of these may ever be used as the test database, and a host-and-port rule alone cannot tell a tunnel apart from a CI service container.

The current workflow, [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml), has two jobs:

- `test`: Ubuntu, Python 3.10 and 3.14, a Postgres service container, `TEST_DATABASE_URL` set, `uv run pytest -q -rs`.
- `docker`: builds the image and smoke-tests it.

Before this decision it ran on push to `main`, on pull requests to `main` and on manual dispatch, and a missing `TEST_DATABASE_URL` silently skipped the database tests.

Alternatives considered:

- A local pre-push hook only. It needs Docker on every machine, and pushes are not verified.
- Testcontainers inside pytest. A new dependency, and still Docker in CI.
- Warnings in the docs only. One pasted URL drops a schema.

## Decision

1. Run the workflow on push to every branch. Keep both jobs.
2. Set `REQUIRE_DB_TESTS=1` in the `test` job, so a missing `TEST_DATABASE_URL` fails the run instead of skipping the suite.
3. Add a guard, called from `tests/conftest.py`, that parses `TEST_DATABASE_URL` with `psycopg.conninfo.conninfo_to_dict` and refuses to run when the database name does not end in `_test`, when it points at the same host, port and database as `DATABASE_URL`, when host or port is missing, when it uses `hostaddr` or `service`, when the host is not loopback, or when it cannot be parsed. The guard errors rather than skips, and never prints either URL.
4. Rename the CI service database and the documented local recipe to `dialogflow_mcp_test`.

## Consequences

- More CI minutes. A push to a branch with an open pull request runs twice.
- Branch protection on `main` (requiring both jobs) and "wait for CI" on the host are manual settings.
- A developer whose throwaway database is remote, has no explicit port or lacks the `_test` suffix cannot run the database tests locally. They use CI, or rename the database.
- To reverse: restore the `main`-only push trigger and the `dialogflow_mcp` database name, remove `REQUIRE_DB_TESTS`, and delete the guard.

Implementation notes: built.

- [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs on push to every branch, on pull requests (this is a public repository, so Actions minutes are free and pull requests from forks need a run) and on manual dispatch. Jobs: `lint` (ruff check, ruff format, mypy, the golden-set overlap check), `test` (Python 3.10 and 3.14, Postgres 17 service with the database `dialogflow_mcp_test`, `REQUIRE_DB_TESTS=1`) and `docker` (build and smoke test).
- The guard is [`tests/_db_guard.py`](../../tests/_db_guard.py), called from [`tests/conftest.py`](../../tests/conftest.py); it also refuses port 5433 (the compose database) and a port equal to `POSTGRES_PORT`, and `PGHOSTADDR`/`PGSERVICE` in the environment. It raises `pytest.UsageError`, and its messages never contain either URL. Unit tests: [`tests/test_db_guard.py`](../../tests/test_db_guard.py).
- `tests/test_db.py` fails at collection when `REQUIRE_DB_TESTS=1` and `TEST_DATABASE_URL` is empty.
