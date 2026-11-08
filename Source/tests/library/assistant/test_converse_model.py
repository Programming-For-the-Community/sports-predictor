"""The Converse adapter: a Messages-API request in, Converse out, and the
reply back in the shape agent_core reads -- including a full tool round."""
from types import SimpleNamespace

from library.assistant.converse_model import ConverseModel

TOOLS = [{"name": "list_events", "description": "List events.", "input_schema": {"type": "object", "properties": {}}}]


class FakeRuntime:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def converse(self, **request):
        self.requests.append(request)
        return self.responses.pop(0)


def _reply(content, stop="end_turn"):
    return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": stop,
            "usage": {"inputTokens": 120, "outputTokens": 30}}


def test_a_request_becomes_converse_and_claude_only_fields_are_dropped():
    runtime = FakeRuntime(_reply([{"text": "Hi."}]))
    model = ConverseModel(runtime)

    response = model.messages.create(
        model="openai.gpt-oss-120b-1:0", max_tokens=512, tools=TOOLS, output_config={"effort": "low"},
        system=[{"type": "text", "text": "Be brief.", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "Hello"}],
    )

    request = runtime.requests[0]
    assert request["modelId"] == "openai.gpt-oss-120b-1:0"
    assert request["system"] == [{"text": "Be brief."}]
    assert request["inferenceConfig"] == {"maxTokens": 512}
    assert request["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {"json": TOOLS[0]["input_schema"]}
    assert request["messages"] == [{"role": "user", "content": [{"text": "Hello"}]}]
    assert "output_config" not in request
    assert response.stop_reason == "end_turn"
    assert response.content[0].text == "Hi."
    assert response.usage.input_tokens == 120


def test_a_tool_round_trips_through_the_next_request():
    runtime = FakeRuntime(
        _reply([{"reasoningContent": {"reasoningText": {"text": "need data"}}},
                {"toolUse": {"toolUseId": "t1", "name": "list_events", "input": {"sport": "nfl"}}}], stop="tool_use"),
        _reply([{"text": "Two games."}]),
    )
    model = ConverseModel(runtime)
    messages = [{"role": "user", "content": "Games?"}]

    first = model.messages.create(model="m", max_tokens=100, tools=TOOLS, messages=messages)
    messages.append({"role": "assistant", "content": first.content})
    messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": '{"events": 2}', "is_error": False}]})
    model.messages.create(model="m", max_tokens=100, tools=TOOLS, messages=messages)

    assert first.stop_reason == "tool_use"
    assert [b.type for b in first.content] == ["tool_use"]
    sent = runtime.requests[1]["messages"]
    assert sent[1] == {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "list_events", "input": {"sport": "nfl"}}}]}
    assert sent[2]["content"][0]["toolResult"] == {"toolUseId": "t1", "content": [{"text": '{"events": 2}'}], "status": "success"}


def test_blank_text_is_left_out_and_errors_and_filters_map_across():
    runtime = FakeRuntime(_reply([{"text": "No."}], stop="content_filtered"))
    model = ConverseModel(runtime)
    previous = [SimpleNamespace(type="text", text="  "), SimpleNamespace(type="tool_use", id="t1", name="x", input=None)]

    response = model.messages.create(model="m", max_tokens=10, messages=[
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": previous},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "boom", "is_error": True}]},
    ])

    sent = runtime.requests[0]["messages"]
    assert sent[1]["content"] == [{"toolUse": {"toolUseId": "t1", "name": "x", "input": {}}}]
    assert sent[2]["content"][0]["toolResult"]["status"] == "error"
    assert response.stop_reason == "refusal"
