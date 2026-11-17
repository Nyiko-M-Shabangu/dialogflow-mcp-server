"""The Dialogflow CX server's detect_intent on a successful call (fake client)."""

from __future__ import annotations

import pytest
from tool_call import call


# ── Dialogflow CX detect_intent success path ──────────────────────────────
def test_cx_detect_intent_success(monkeypatch):
    from google.cloud import dialogflowcx_v3 as cx

    from dialogflow_mcp import server as cx_server

    monkeypatch.setenv("GCP_PROJECT", "demo-project")
    monkeypatch.setenv("DF_AGENT_ID", "agent-123")
    monkeypatch.setenv("DF_LOCATION", "europe-west2")
    captured = {}

    class FakeClient:
        def session_path(self, project, location, agent, session):
            captured["path_args"] = (project, location, agent, session)
            return f"projects/{project}/locations/{location}/agents/{agent}/sessions/{session}"

        def detect_intent(self, request):
            captured["request"] = request
            result = cx.QueryResult(
                response_messages=[
                    cx.ResponseMessage(text=cx.ResponseMessage.Text(text=["Added", "2 Kota."])),
                    cx.ResponseMessage(),  # non-text message is skipped
                ],
                match=cx.Match(intent=cx.Intent(display_name="order.add"), confidence=0.9),
                current_page=cx.Page(display_name="Order Page"),
                parameters={"food": "Kota", "qty": 2},
            )
            return cx.DetectIntentResponse(query_result=result)

    def fake_sessions_client(location):
        captured["location"] = location
        return FakeClient()

    monkeypatch.setattr(cx_server, "_sessions_client", fake_sessions_client)
    res = call(cx_server.detect_intent, "two kota please", session_id="s-1", language_code="en")

    assert captured["location"] == "europe-west2"
    assert captured["path_args"] == ("demo-project", "europe-west2", "agent-123", "s-1")
    assert captured["request"].query_input.text.text == "two kota please"
    assert captured["request"].query_input.language_code == "en"
    assert res["session_id"] == "s-1"
    assert res["replies"] == ["Added 2 Kota."]
    assert res["intent"] == "order.add"
    assert res["confidence"] == pytest.approx(0.9)
    assert res["page"] == "Order Page"
    assert res["parameters"] == {"food": "Kota", "qty": 2.0}
