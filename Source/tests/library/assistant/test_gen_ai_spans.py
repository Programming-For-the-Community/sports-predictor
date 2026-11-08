"""The GenAI spans AgentCore Evaluations reads: one invoke_agent span per
answered turn, a chat span per model call and an execute_tool span per tool
call, all parented to the turn and all ended."""
import json
import sys
from types import SimpleNamespace

import pytest
from _turn_fakes import (
    PUBLIC,
    FakeGuardrails,
    FakeModel,
    _call,
    _response,
    _text,
    make_clients,
    run,
)

from library.assistant import gen_ai_spans


class RecordedSpan:
    def __init__(self, name, attributes, parent):
        self.name = name
        self.attributes = dict(attributes)
        self.parent = parent
        self.ended = False

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def end(self):
        self.ended = True


class RecordingTracer:
    def __init__(self):
        self.spans = []

    def start(self, name, attributes, parent=None):
        span = RecordedSpan(name, attributes, parent)
        self.spans.append(span)
        return span

    def by_operation(self, operation):
        return [s for s in self.spans if s.attributes["gen_ai.operation.name"] == operation]


@pytest.fixture
def traced(fake_table):
    """Clients for a turn, recording their spans."""
    def build(model, guardrails=None):
        clients, _ = make_clients(model, fake_table(), guardrails)
        clients.tracer = RecordingTracer()
        return clients
    return build


def test_an_answered_turn_records_the_turn_model_calls_and_tool_calls(traced):
    model = FakeModel([
        _response(_call("get_event_prediction", {"sport": "nfl", "event_id": "1"}), stop="tool_use"),
        _response(_text("DET -6.5, 71% to win.")),
    ])
    clients = traced(model)

    run(PUBLIC, clients)

    tracer = clients.tracer
    (turn,) = tracer.by_operation("invoke_agent")
    assert turn.name == "invoke_agent assistant_public"
    assert turn.attributes["gen_ai.task.input"] == "Why is DET favoured?"
    assert turn.attributes["gen_ai.task.output"] == "DET -6.5, 71% to win."
    assert turn.attributes["session.id"] == turn.attributes["gen_ai.conversation.id"]

    chats = tracer.by_operation("chat")
    assert len(chats) == 2
    assert chats[0].attributes["gen_ai.request.model"] == "anthropic.claude-haiku-5-5"
    assert "get_event_prediction" in chats[0].attributes["gen_ai.tool.definitions"]
    first_output = json.loads(chats[0].attributes["gen_ai.output.messages"])
    assert first_output[0]["parts"][0] == {
        "type": "tool_call", "id": "t1", "name": "get_event_prediction", "arguments": {"sport": "nfl", "event_id": "1"},
    }

    (tool,) = tracer.by_operation("execute_tool")
    assert tool.attributes["gen_ai.tool.name"] == "get_event_prediction"
    assert json.loads(tool.attributes["gen_ai.tool.call.arguments"]) == {"sport": "nfl", "event_id": "1"}
    assert json.loads(tool.attributes["gen_ai.tool.call.result"]) == {"value": 1}
    assert "error.type" not in tool.attributes

    assert all(s.parent is turn for s in chats + [tool])
    assert all(s.ended for s in tracer.spans)


def test_a_failed_model_call_ends_both_spans_as_errors(traced):
    class BrokenModel(FakeModel):
        def create(self, **kwargs):
            raise RuntimeError("model unavailable")

    clients = traced(BrokenModel([]))

    events = run(PUBLIC, clients)

    assert events[-1]["code"] == "failed"
    (turn,) = clients.tracer.by_operation("invoke_agent")
    (chat,) = clients.tracer.by_operation("chat")
    assert turn.attributes["error.type"] == "failed"
    assert chat.attributes["error.type"] == "model_error"
    assert turn.ended and chat.ended


def test_a_blocked_question_records_nothing(traced):
    clients = traced(FakeModel([]), guardrails=FakeGuardrails(block_input=True))

    run(PUBLIC, clients)

    assert clients.tracer.spans == []


def test_without_opentelemetry_the_null_tracer_is_used(monkeypatch):
    monkeypatch.setitem(sys.modules, "opentelemetry", None)

    assert gen_ai_spans.otel_tracer() is gen_ai_spans.NULL_TRACER


def test_the_otel_adapter_uses_the_scope_and_parents_explicitly(monkeypatch):
    started = []
    trace_api = SimpleNamespace(
        get_tracer=lambda scope: SimpleNamespace(scope=scope, start_span=lambda name, context, attributes: started.append((name, context)) or name),
        set_span_in_context=lambda parent: ("context-of", parent),
    )
    monkeypatch.setitem(sys.modules, "opentelemetry", SimpleNamespace(trace=trace_api))

    tracer = gen_ai_spans.otel_tracer()
    turn = tracer.start("invoke_agent x", {})
    tracer.start("chat m", {}, turn)

    assert tracer._tracer.scope.startswith("opentelemetry.instrumentation.")
    assert started == [("invoke_agent x", None), ("chat m", ("context-of", "invoke_agent x"))]


@pytest.mark.parametrize("ok, has_error", [(True, False), (False, True)])
def test_tool_errors_are_marked(ok, has_error):
    tracer = RecordingTracer()
    span = gen_ai_spans.start_tool(tracer, None, SimpleNamespace(name="list_events", id="t1", input=None))
    gen_ai_spans.end_tool(span, "{}", ok)
    assert ("error.type" in span.attributes) is has_error
    assert span.attributes["gen_ai.tool.call.arguments"] == "{}"
