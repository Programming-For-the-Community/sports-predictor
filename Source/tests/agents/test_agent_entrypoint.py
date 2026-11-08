"""
Source/agents/assistant/agent.py wiring: the runtime entrypoint builds the
profile, streams agent_core's events, and writes one bare-JSON turn line per
finished turn. The Anthropic and AgentCore SDKs and the client builder are
stubbed -- the turn is covered by tests/library/assistant/test_agent_core.py,
the clients by test_runtime.py, and the real SDKs by the image build.
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


CLIENTS = types.SimpleNamespace()


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
    monkeypatch.setenv("ASSISTANT_PROFILE", "public")
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(AnthropicBedrock=MagicMock()))
    monkeypatch.setitem(sys.modules, "bedrock_agentcore", types.SimpleNamespace(BedrockAgentCoreApp=_App))
    from library.assistant import runtime
    monkeypatch.setattr(runtime, "build_clients", MagicMock(return_value=CLIENTS))
    spec = importlib.util.spec_from_file_location("assistant_agent_under_test", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_entrypoint_streams_events_and_logs_one_json_line_per_turn(agent, caplog):
    events = [{"type": "progress", "tool": "list_events"}, {"type": "answer", "text": "DET -6.5", "sources": ["list_events"], "questions_left": 49}]
    context = types.SimpleNamespace(request_headers={"Authorization": "Bearer h.eyJzdWIiOiJ1LTEifQ.s"}, session_id="s" * 36)
    agent.turn_logger.propagate = True

    with patch.object(agent.runtime, "profile_for", return_value="profile"), \
            patch.object(agent.agent_core, "handle", return_value=iter(events)) as handle, \
            caplog.at_level(logging.INFO, logger="assistant-turns"):
        streamed = list(agent.app.entry({"message": "Why DET?"}, context))

    assert streamed == events
    assert handle.call_args.args[:2] == ("profile", CLIENTS)
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "assistant-turns"]
    assert len(lines) == 1
    assert lines[0]["user_sub"] == "u-1"
    assert lines[0]["outcome"] == "answered"
    assert lines[0]["question"] == "Why DET?"


def test_the_clients_get_a_tracer(agent):
    # OpenTelemetry is installed only in the agent image; without it the
    # turn records nothing.
    assert hasattr(agent.clients.tracer, "start")
