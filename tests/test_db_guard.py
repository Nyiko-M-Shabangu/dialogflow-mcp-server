"""The TEST_DATABASE_URL guard in tests/_db_guard.py (pure, no database)."""

from __future__ import annotations

import pytest
from _db_guard import unsafe_test_db_reason


class TestTestDbGuard:
    CI = "postgresql://mcp:ci_only_password@localhost:5432/dialogflow_mcp_test"
    README = "postgresql://mcp:mcp_local_dev@localhost:55434/dialogflow_mcp_test"

    @staticmethod
    def _uri(host="localhost", port="5432", db="dialogflow_mcp_test", query=""):
        netloc = f"{host}:{port}" if port is not None else host
        return f"postgresql://mcp:ci_only_password@{netloc}/{db}{query}"

    def test_ci_url_allowed(self):
        assert unsafe_test_db_reason(self.CI, "", None) is None

    def test_readme_url_allowed(self):
        assert unsafe_test_db_reason(self.README, None, None) is None

    def test_name_without_test_suffix_rejected(self):
        reason = unsafe_test_db_reason(self._uri(db="dialogflow_mcp"), "", None)
        assert reason and "_test" in reason

    def test_equal_to_ambient_written_differently(self):
        ambient = (
            "postgresql://mcp:mcp_local_dev@127.0.0.1:5432/dialogflow_mcp_test?sslmode=disable"
        )
        reason = unsafe_test_db_reason(self.CI, ambient, None)
        assert reason and "DATABASE_URL" in reason

    def test_portless_ambient_matches_default_port(self):
        ambient = "postgresql://mcp:mcp_local_dev@localhost/dialogflow_mcp_test"
        reason = unsafe_test_db_reason(self.CI, ambient, None)
        assert reason and "DATABASE_URL" in reason

    def test_hostless_ambient_matches_localhost(self):
        ambient = "postgresql://mcp:mcp_local_dev@:5432/dialogflow_mcp_test"
        reason = unsafe_test_db_reason(self.CI, ambient, None)
        assert reason and "DATABASE_URL" in reason

    def test_ambient_with_zero_padded_port_matches(self):
        ambient = self._uri(port="05432")
        reason = unsafe_test_db_reason(self.CI, ambient, None)
        assert reason and "DATABASE_URL" in reason

    def test_different_ambient_db_allowed(self):
        ambient = self._uri(db="other_db")
        assert unsafe_test_db_reason(self.CI, ambient, None) is None

    @pytest.mark.parametrize("port", ["5433", "05433", " 5433", "5433 "])
    def test_compose_port_5433_rejected_in_any_spelling(self, port):
        dsn = f"host=localhost port='{port}' dbname=dialogflow_mcp_test"
        reason = unsafe_test_db_reason(dsn, "", None)
        assert reason and "5433" in reason

    def test_compose_port_5433_uri_rejected(self):
        reason = unsafe_test_db_reason(self._uri(port="5433"), "", None)
        assert reason and "5433" in reason

    def test_port_equal_to_postgres_port_rejected(self):
        reason = unsafe_test_db_reason(self.README, "", "55434")
        assert reason and "POSTGRES_PORT" in reason
        assert unsafe_test_db_reason(self.README, "", "5999") is None

    @pytest.mark.parametrize("pg_port", ["055434", " 55434", "55434 "])
    def test_postgres_port_compared_as_integer(self, pg_port):
        reason = unsafe_test_db_reason(self.README, "", pg_port)
        assert reason and "POSTGRES_PORT" in reason

    def test_unparseable_postgres_port_ignored(self):
        assert unsafe_test_db_reason(self.README, "", "abc") is None

    def test_missing_port_rejected(self):
        reason = unsafe_test_db_reason(self._uri(port=None), "", None)
        assert reason and "port explicitly" in reason

    def test_non_numeric_port_rejected(self):
        dsn = "host=localhost port=abc dbname=app_test"
        reason = unsafe_test_db_reason(dsn, "", None)
        assert reason and "single number" in reason

    def test_multi_port_rejected(self):
        dsn = "host=localhost,localhost port=5432,5433 dbname=app_test"
        reason = unsafe_test_db_reason(dsn, "", None)
        assert reason and "single number" in reason

    def test_missing_host_rejected(self):
        reason = unsafe_test_db_reason(self._uri(host=""), "", None)
        assert reason and "host explicitly" in reason

    def test_host_query_override_to_remote_rejected(self):
        url = self._uri(query="?host=db.example.com")
        reason = unsafe_test_db_reason(url, "", None)
        assert reason and "host must be localhost" in reason

    def test_hostaddr_rejected(self):
        reason = unsafe_test_db_reason(self._uri(query="?hostaddr=10.0.0.5"), "", None)
        assert reason and "hostaddr or service" in reason

    def test_service_rejected(self):
        reason = unsafe_test_db_reason(self._uri(query="?service=prod"), "", None)
        assert reason and "hostaddr or service" in reason

    @pytest.mark.parametrize("var", ["PGHOSTADDR", "PGSERVICE"])
    def test_pg_env_override_rejected(self, var):
        secret = "10.9.8.7"
        reason = unsafe_test_db_reason(self.CI, "", None, {var: secret})
        assert reason and "PGHOSTADDR" in reason and secret not in reason

    def test_empty_pg_env_allowed(self):
        env = {"PGHOSTADDR": "", "PGSERVICE": ""}
        assert unsafe_test_db_reason(self.CI, "", None, env) is None

    def test_keyword_form_remote_host_rejected(self):
        dsn = "host=db.example.com port=5432 dbname=app_test user=mcp"
        reason = unsafe_test_db_reason(dsn, "", None)
        assert reason and "host must be localhost" in reason

    def test_neon_style_host_rejected(self):
        url = self._uri(host="ep-cool-name-123456.us-east-2.aws.neon.tech", db="app_test")
        reason = unsafe_test_db_reason(url, "", None)
        assert reason and "host must be localhost" in reason

    def test_unparseable_rejected(self):
        reason = unsafe_test_db_reason("not a url ===", "", None)
        assert reason and "cannot be parsed" in reason

    def test_reasons_never_contain_the_password(self):
        word = "zebracrossing"
        ok = f"host=localhost port=5432 dbname=app_test password={word}"
        cases = [
            # (test dsn, ambient, POSTGRES_PORT, env, expected reason substring)
            (f"host=localhost port=5432 dbname=app password={word}", "", None, None, "_test"),
            (
                f"host=db.example.com port=5432 dbname=app_test password={word}",
                "",
                None,
                None,
                "host must be",
            ),
            (f"host=localhost port=5433 dbname=app_test password={word}", "", None, None, "5433"),
            (f"host=localhost dbname=app_test password={word}", "", None, None, "port explicitly"),
            (f"{ok} hostaddr=1.2.3.4", "", None, None, "hostaddr or service"),
            (ok, "", "5432", None, "POSTGRES_PORT"),
            (ok, ok, None, None, "DATABASE_URL"),
            (ok, "", None, {"PGSERVICE": word}, "PGHOSTADDR"),
            (
                f"host=localhost port=x dbname=app_test password={word}",
                "",
                None,
                None,
                "single number",
            ),
            (f"=== password={word}", "", None, None, "cannot be parsed"),
        ]
        for dsn, ambient, pg_port, env, expected in cases:
            reason = unsafe_test_db_reason(dsn, ambient, pg_port, env)
            assert reason and expected in reason and word not in reason
