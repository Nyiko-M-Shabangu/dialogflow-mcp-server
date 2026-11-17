"""The /ready readiness route: database check, config check, timeout, and
that it is reachable without a token."""

from __future__ import annotations

import asyncio
import json

import pytest

from dialogflow_mcp import remote_server


# ── /ready readiness route ─────────────────────────────────────────────────
class TestReadiness:
    ENV = {
        "DATABASE_URL": "postgresql://u:p@db.example/x",
        "GCP_PROJECT": "demo-project",
        "GOOGLE_APPLICATION_CREDENTIALS": "/run/key.json",
    }

    @pytest.fixture
    def full_env(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", raising=False)
        for k, v in self.ENV.items():
            monkeypatch.setenv(k, v)

    @staticmethod
    def _call():
        res = asyncio.run(remote_server.readiness_response())
        return res.status_code, json.loads(res.body)

    def test_ready_when_db_answers_and_config_complete(self, monkeypatch, full_env):
        from dialogflow_mcp import database

        monkeypatch.setattr(database, "fetch_one", lambda *_a, **_k: {"ok": 1})
        assert self._call() == (
            200,
            {"status": "ready", "checks": {"database": "ok", "config": "ok"}},
        )

    def test_503_when_db_fails_without_leaking_error(self, monkeypatch, full_env):
        from dialogflow_mcp import database

        def fail(*_a, **_k):
            raise RuntimeError("connection to postgresql://u:p@db.example failed")

        monkeypatch.setattr(database, "fetch_one", fail)
        status, body = self._call()
        assert status == 503
        assert body == {"status": "unavailable", "checks": {"database": "fail", "config": "ok"}}

    def test_503_when_db_hangs(self, monkeypatch, full_env):
        import time

        from dialogflow_mcp import database

        monkeypatch.setattr(remote_server, "READY_TIMEOUT_S", 0.2)
        monkeypatch.setattr(database, "fetch_one", lambda *_a, **_k: time.sleep(1))
        status, body = self._call()
        assert status == 503 and body["checks"]["database"] == "fail"

    def test_503_when_config_incomplete(self, monkeypatch, full_env):
        from dialogflow_mcp import database

        monkeypatch.setattr(database, "fetch_one", lambda *_a, **_k: {"ok": 1})
        monkeypatch.delenv("GCP_PROJECT")
        status, body = self._call()
        assert status == 503 and body["checks"] == {"database": "ok", "config": "incomplete"}

    def test_ready_is_public(self):
        assert "/ready" in remote_server.PUBLIC_PATHS

        inner_called = {}

        async def inner(scope, receive, send):
            inner_called["yes"] = True

        mw = remote_server.BearerAuthASGIMiddleware(inner, token="t" * 32)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_event):
            pass

        scope = {"type": "http", "path": "/ready", "method": "GET", "headers": [], "state": {}}
        asyncio.run(mw(scope, receive, send))
        assert inner_called.get("yes") is True
