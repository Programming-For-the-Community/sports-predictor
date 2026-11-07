"""
Source/agents/assistant/agent.py wiring: the runtime entrypoint builds the
profile, streams agent_core's events, and writes one bare-JSON turn line per
finished turn. The Anthropic and AgentCore SDKs are stubbed -- the turn
itself is covered by tests/library/assistant/test_agent_core.py, and the
real SDKs by the image build.
"""
import importlib.util
import json
import logging
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

AGENT_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "agents", "assistant", "agent.py"))


class _App:
    def __init__(self):
        self.entry = None

    def entrypoint(self, func):
        self.entry = func
        return func

    def run(self):
        """Not called in tests."""


@pytest.fixture
def agent(monkeypatch):
    env = {
        "ASSISTANT_PROFILE": "public", "AWS_REGION": "us-east-2", "SPORT_REGISTRY_TABLE_NAME": "registry",
        "ASSISTANT_TABLE_NAME": "assistant", "GUARDRAIL_ID": "g", "GUARDRAIL_VERSION": "1", "MEMORY_ID": "m",
        "PUBLIC_TOOLS_FUNCTION_NAME": "public-tools", "ADMIN_TOOLS_FUNCTION_NAME": "",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(AnthropicBedrockMantle=lambda **kwargs: object()))
    monkeypatch.setitem(sys.modules, "bedrock_agentcore", types.SimpleNamespace(BedrockAgentCoreApp=_App))
    # The library's pinned boto3 predates the bedrock-agentcore service; the
    # clients are never called here.
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: MagicMock())
    monkeypatch.setattr("boto3.resource", lambda *args, **kwargs: MagicMock())
    spec = importlib.util.spec_from_file_location("assistant_agent_under_test", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_entrypoint_streams_events_and_logs_one_json_line_per_turn(agent, caplog):
    events = [{"type": "progress", "tool": "list_events"}, {"type": "answer", "text": "DET -6.5", "sources": ["list_events"], "questions_left": 49}]
    context = types.SimpleNamespace(request_headers={"Authorization": "Bearer h.eyJzdWIiOiJ1LTEifQ.s"}, session_id="s" * 36)
    agent.turn_logger.propagate = True

    with patch.object(agent, "sports", lambda: ["nfl"]), patch.object(agent.agent_core, "handle", lambda *a: iter(events)), \
            caplog.at_level(logging.INFO, logger="assistant-turns"):
        streamed = list(agent.app.entry({"message": "Why DET?"}, context))

    assert streamed == events
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "assistant-turns"]
    assert len(lines) == 1
    assert lines[0]["user_sub"] == "u-1" and lines[0]["outcome"] == "answered" and lines[0]["question"] == "Why DET?"


def test_tools_go_to_the_public_lambda_unless_they_are_admin_tools(agent, monkeypatch):
    calls = []

    class FakeLambda:
        def invoke(self, FunctionName, Payload):
            calls.append((FunctionName, json.loads(Payload)))
            return {"Payload": types.SimpleNamespace(read=lambda: b'{"ok": true, "result": {}}')}

    monkeypatch.setenv("ADMIN_TOOLS_FUNCTION_NAME", "admin-tools")
    with patch.object(agent, "_lambda", FakeLambda()):
        assert agent.invoke_tool("list_events", {"sport": "nfl"}) == {"ok": True, "result": {}}
        agent.invoke_tool("get_cost_summary", {})

    assert [name for name, _ in calls] == ["public-tools", "admin-tools"]
    assert calls[0][1] == {"tool": "list_events", "input": {"sport": "nfl"}}


def test_a_tool_lambda_crash_becomes_a_message(agent):
    class Crashing:
        def invoke(self, **kwargs):
            return {"FunctionError": "Unhandled", "Payload": types.SimpleNamespace(read=lambda: b"{}")}

    with patch.object(agent, "_lambda", Crashing()):
        assert agent.invoke_tool("list_events", {})["ok"] is False
