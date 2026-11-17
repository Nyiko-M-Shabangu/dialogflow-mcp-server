"""Shared pytest configuration.

The DB integration tests drop and recreate the `public` schema, so a
TEST_DATABASE_URL that could point at a real database is refused here, before
any test module imports the package. When it is accepted, DATABASE_URL is set
to it so the connection pool uses the test database.
"""

import os

import pytest
from _db_guard import unsafe_test_db_reason

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

if TEST_DATABASE_URL:
    # Never put either URL in the message.
    _reason = unsafe_test_db_reason(
        TEST_DATABASE_URL,
        os.environ.get("DATABASE_URL", ""),
        os.environ.get("POSTGRES_PORT"),
        os.environ,
    )
    if _reason:
        # pytest reports this as a usage error and exits before running tests.
        raise pytest.UsageError(_reason)
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# Never let unit tests pick up a real GCP project / agent from the shell.
for _var in (
    "GCP_PROJECT",
    "DF_AGENT_ID",
    "DF_ENVIRONMENT",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON",
):
    os.environ.pop(_var, None)
os.environ.setdefault("DF_LOCATION", "global")
