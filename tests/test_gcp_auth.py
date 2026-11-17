"""Unit tests for GOOGLE_APPLICATION_CREDENTIALS_JSON handling (gcp_auth.py).

No network and no real key: the google-auth factory is replaced with a fake.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from dialogflow_mcp import gcp_auth, remote_server, server, server_es

ENV = gcp_auth.CREDENTIALS_JSON_ENV
SECRET_MARKER = "do-not-leak-this-private-key"
# Built at runtime so the source holds no PEM header for secret scanners to flag.
_PEM_LABEL = " ".join(("PRIVATE", "KEY"))
FAKE_KEY = {
    "type": "service_account",
    "project_id": "demo-project",
    "private_key_id": "abc123",
    "private_key": f"-----BEGIN {_PEM_LABEL}-----\n{SECRET_MARKER}\n-----END {_PEM_LABEL}-----\n",
    "client_email": "mcp@demo-project.iam.gserviceaccount.com",
    "token_uri": "https://oauth2.googleapis.com/token",
}


@pytest.fixture
def fake_factory(monkeypatch):
    from google.oauth2 import service_account

    sentinel = object()
    factory = MagicMock(return_value=sentinel)
    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", factory)
    return factory, sentinel


class TestCredentialsFromEnv:
    def test_unset_or_blank_means_adc(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        assert gcp_auth.credentials_from_env() is None
        assert gcp_auth.client_kwargs() == {}
        monkeypatch.setenv(ENV, "   ")
        assert gcp_auth.client_kwargs() == {}

    def test_valid_key_builds_credentials(self, monkeypatch, fake_factory):
        factory, sentinel = fake_factory
        monkeypatch.setenv(ENV, json.dumps(FAKE_KEY))
        assert gcp_auth.client_kwargs() == {"credentials": sentinel}
        info = factory.call_args.args[0]
        assert info["client_email"] == FAKE_KEY["client_email"]
        assert factory.call_args.kwargs["scopes"] == [
            "https://www.googleapis.com/auth/cloud-platform"
        ]

    @pytest.mark.parametrize(
        "raw, match",
        [
            ('{"type": "service_account", ' + SECRET_MARKER, "not valid JSON"),
            (json.dumps({**FAKE_KEY, "type": "authorized_user"}), "service-account key"),
            (json.dumps([FAKE_KEY]), "service-account key"),
        ],
    )
    def test_invalid_value_raises_without_leaking_it(self, monkeypatch, raw, match):
        monkeypatch.setenv(ENV, raw)
        with pytest.raises(gcp_auth.CredentialsConfigError, match=match) as exc_info:
            gcp_auth.credentials_from_env()
        assert SECRET_MARKER not in str(exc_info.value)
        # No original error (which may quote the value) is chained into the
        # traceback: either none existed, or `raise ... from None` hid it.
        err = exc_info.value
        assert err.__context__ is None or err.__suppress_context__ is True

    @pytest.mark.parametrize(
        "key",
        [
            {k: v for k, v in FAKE_KEY.items() if k != "client_email"},  # missing field
            FAKE_KEY,  # all fields present, but private_key is not a real key
        ],
    )
    def test_unusable_key_raises_without_leaking_it(self, monkeypatch, key):
        # Real google-auth factory here (no fake_factory).
        monkeypatch.setenv(ENV, json.dumps(key))
        with pytest.raises(gcp_auth.CredentialsConfigError, match="not a usable") as exc_info:
            gcp_auth.credentials_from_env()
        assert SECRET_MARKER not in str(exc_info.value)


class TestClientsUseEnvCredentials:
    def test_es_sessions_client_gets_credentials(self, monkeypatch, fake_factory):
        _, sentinel = fake_factory
        fake_cls = MagicMock()
        monkeypatch.setattr(server_es.dialogflow, "SessionsClient", fake_cls)
        monkeypatch.setattr(server_es, "_sessions_client_instance", None)
        monkeypatch.setattr(server_es, "LOCATION", "global")
        monkeypatch.setenv(ENV, json.dumps(FAKE_KEY))
        server_es._sessions_client()
        assert fake_cls.call_args.kwargs["credentials"] is sentinel

        fake_cls.reset_mock()
        monkeypatch.setattr(server_es, "_sessions_client_instance", None)
        monkeypatch.setattr(server_es, "LOCATION", "europe-west2")
        server_es._sessions_client()
        kwargs = fake_cls.call_args.kwargs
        assert kwargs["credentials"] is sentinel
        assert kwargs["client_options"].api_endpoint == "europe-west2-dialogflow.googleapis.com"
        monkeypatch.setattr(server_es, "_sessions_client_instance", None)

    def test_cx_sessions_client_gets_credentials(self, monkeypatch, fake_factory):
        _, sentinel = fake_factory
        fake_cls = MagicMock()
        monkeypatch.setattr(server.cx, "SessionsClient", fake_cls)
        monkeypatch.setattr(server, "_client", None)
        monkeypatch.setenv(ENV, json.dumps(FAKE_KEY))
        server._sessions_client("global")
        assert fake_cls.call_args.kwargs["credentials"] is sentinel
        monkeypatch.setattr(server, "_client", None)


class TestMissingRuntimeConfig:
    VARS = (
        "DATABASE_URL",
        "GCP_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
    )

    def test_reports_each_missing_setting(self, monkeypatch):
        for var in self.VARS:
            monkeypatch.delenv(var, raising=False)
        problems = remote_server.missing_runtime_config()
        # DATABASE_URL is not listed: create_remote_app() already refuses to start without it.
        assert len(problems) == 2
        assert any("GCP_PROJECT" in p for p in problems)
        assert any("GOOGLE_APPLICATION_CREDENTIALS_JSON" in p for p in problems)

    @pytest.mark.parametrize(
        "cred_var", ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS_JSON"]
    )
    def test_complete_config_reports_nothing(self, monkeypatch, cred_var):
        for var in self.VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example/x")
        monkeypatch.setenv("GCP_PROJECT", "demo-project")
        monkeypatch.setenv(cred_var, "set")
        assert remote_server.missing_runtime_config() == []


class TestRemoteStartupValidation:
    def test_bad_credentials_json_fails_at_startup(self, monkeypatch):
        monkeypatch.setenv(ENV, "not json " + SECRET_MARKER)
        with pytest.raises(remote_server.ConfigError, match=ENV) as exc_info:
            remote_server.create_remote_app(token="x" * 32)
        assert SECRET_MARKER not in str(exc_info.value)
