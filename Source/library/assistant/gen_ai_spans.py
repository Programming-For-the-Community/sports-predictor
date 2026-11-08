"""
OpenTelemetry GenAI spans for one assistant turn -- what AgentCore
Evaluations reads to score answers (Terraform/agentcore-evaluations.tf).
Its generic-framework support needs a scope name under
opentelemetry.instrumentation.* and gen_ai.operation.name on every span:
invoke_agent for the turn, chat for each model call, execute_tool for each
tool call, with the conversation in the gen_ai.* attributes set here.

agent_core takes a tracer with one method, start(name, attributes, parent)
-> span (set_attribute, end). otel_tracer() adapts OpenTelemetry's in the
agent image; everywhere else (tests, the eval runner, environments without
OpenTelemetry) NULL_TRACER records nothing.

Spans land in the runtime's own log group (unified telemetry), kept 14
days like the turn transcripts. Thinking blocks and the system prompt are
left out.
"""
from __future__ import annotations

import json

SCOPE = "opentelemetry.instrumentation.sports_predictor_assistant"
PROVIDER = "aws.bedrock"
OPERATION = "gen_ai.operation.name"


class _NullSpan:
    def set_attribute(self, key, value) -> None:
        """Records nothing."""

    def end(self) -> None:
        """Records nothing."""


class _NullTracer:
    def start(self, *_args, **_kwargs) -> _NullSpan:
        """Same call as the OpenTelemetry adapter's start; records nothing."""
        return _NullSpan()


NULL_TRACER = _NullTracer()


class _OtelTracer:
    def __init__(self, trace_api) -> None:
        self._trace = trace_api
        self._tracer = trace_api.get_tracer(SCOPE)

    def start(self, name: str, attributes: dict, parent=None):
        context = self._trace.set_span_in_context(parent) if parent is not None else None
        return self._tracer.start_span(name, context=context, attributes=attributes)


def otel_tracer():
    """The OpenTelemetry tracer, or NULL_TRACER where OpenTelemetry isn't
    installed. Spans are parented explicitly, never made current, because a
    turn's generator is resumed across the runtime's streaming writes."""
    try:
        from opentelemetry import trace
    except ImportError:
        return NULL_TRACER
    return _OtelTracer(trace)


def _json(value) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _parts(blocks) -> list[dict]:
    """A response's text and tool calls in the GenAI message-part shape."""
    parts = []
    for block in blocks:
        if block.type == "text":
            parts.append({"type": "text", "content": block.text})
        elif block.type == "tool_use":
            parts.append({"type": "tool_call", "id": block.id, "name": block.name, "arguments": block.input})
    return parts


def start_turn(tracer, agent: str, session_id: str, question: str):
    return tracer.start(f"invoke_agent {agent}", {
        OPERATION: "invoke_agent",
        "gen_ai.provider.name": PROVIDER,
        "gen_ai.agent.name": agent,
        "gen_ai.conversation.id": session_id,
        "session.id": session_id,
        "gen_ai.task.input": question,
    })


def end_turn(span, answer: str | None) -> None:
    if answer is None:
        span.set_attribute("error.type", "failed")
    else:
        span.set_attribute("gen_ai.task.output", answer)
    span.end()


def start_chat(tracer, parent, model_id: str, tools: list[dict]):
    return tracer.start(f"chat {model_id}", {
        OPERATION: "chat",
        "gen_ai.provider.name": PROVIDER,
        "gen_ai.request.model": model_id,
        "gen_ai.tool.definitions": _json(tools),
    }, parent)


def end_chat(span, response) -> None:
    span.set_attribute("gen_ai.response.finish_reasons", [str(response.stop_reason)])
    span.set_attribute("gen_ai.output.messages", _json([
        {"role": "assistant", "parts": _parts(response.content), "finish_reason": str(response.stop_reason)},
    ]))
    usage = getattr(response, "usage", None)
    if usage is not None:
        span.set_attribute("gen_ai.usage.input_tokens", int(getattr(usage, "input_tokens", 0) or 0))
        span.set_attribute("gen_ai.usage.output_tokens", int(getattr(usage, "output_tokens", 0) or 0))
    span.end()


def start_tool(tracer, parent, call):
    return tracer.start(f"execute_tool {call.name}", {
        OPERATION: "execute_tool",
        "gen_ai.tool.name": call.name,
        "gen_ai.tool.type": "function",
        "gen_ai.tool.call.id": call.id,
        "gen_ai.tool.call.arguments": _json(call.input or {}),
    }, parent)


def end_tool(span, result: str, ok: bool) -> None:
    span.set_attribute("gen_ai.tool.call.result", result)
    if not ok:
        span.set_attribute("error.type", "tool_error")
    span.end()
