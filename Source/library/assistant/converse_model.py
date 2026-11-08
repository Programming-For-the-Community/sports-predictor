"""
Any Bedrock model through the Converse API, behind the small part of the
Anthropic client agent_core uses: model.messages.create(**request) with a
Messages-API request, returning a response with stop_reason, content
blocks (.type "text" or "tool_use") and usage. runtime.model_client picks
this for every non-Claude ASSISTANT_MODEL_ID; Claude keeps the Anthropic
client.

Claude-only request fields (cache_control, output_config) are dropped.
Reasoning blocks some models return are not kept: answers and tool calls
are what the next request needs.
"""
from __future__ import annotations

from types import SimpleNamespace

STOP_REASONS = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "guardrail_intervened": "refusal",
    "content_filtered": "refusal",
}


def _block_dict(block) -> dict:
    """A content block as a dict, whether it came from a request (dict) or
    from a previous response (SimpleNamespace)."""
    return block if isinstance(block, dict) else vars(block)


def _to_converse_block(block: dict) -> dict | None:
    kind = block.get("type")
    if kind == "text":
        return {"text": block["text"]} if block["text"].strip() else None
    if kind == "tool_use":
        return {"toolUse": {"toolUseId": block["id"], "name": block["name"], "input": block.get("input") or {}}}
    if kind == "tool_result":
        return {"toolResult": {
            "toolUseId": block["tool_use_id"],
            "content": [{"text": str(block.get("content") or "")}],
            "status": "error" if block.get("is_error") else "success",
        }}
    return None


def _to_converse_message(message: dict) -> dict:
    content = message["content"]
    if isinstance(content, str):
        blocks = [{"text": content}]
    else:
        blocks = [b for b in (_to_converse_block(_block_dict(c)) for c in content) if b is not None]
    return {"role": message["role"], "content": blocks}


def _tool_config(tools: list[dict]) -> dict:
    return {"tools": [
        {"toolSpec": {"name": t["name"], "description": t["description"], "inputSchema": {"json": t["input_schema"]}}}
        for t in tools
    ]}


def _from_converse_block(block: dict):
    if "text" in block:
        return SimpleNamespace(type="text", text=block["text"])
    if "toolUse" in block:
        use = block["toolUse"]
        return SimpleNamespace(type="tool_use", id=use["toolUseId"], name=use["name"], input=use.get("input") or {})
    return None


class _Messages:
    def __init__(self, runtime_client) -> None:
        self._runtime = runtime_client

    def create(self, *, model: str, max_tokens: int, messages: list[dict], system=None, tools=None, **_claude_only):
        request = {
            "modelId": model,
            "messages": [_to_converse_message(m) for m in messages],
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if system:
            request["system"] = [{"text": s["text"]} for s in system]
        if tools:
            request["toolConfig"] = _tool_config(tools)
        response = self._runtime.converse(**request)
        blocks = response["output"]["message"]["content"]
        usage = response.get("usage", {})
        return SimpleNamespace(
            stop_reason=STOP_REASONS.get(response.get("stopReason"), "end_turn"),
            content=[b for b in (_from_converse_block(block) for block in blocks) if b is not None],
            usage=SimpleNamespace(input_tokens=usage.get("inputTokens", 0), output_tokens=usage.get("outputTokens", 0)),
        )


class ConverseModel:
    """runtime_client: a boto3 bedrock-runtime client."""

    def __init__(self, runtime_client) -> None:
        self.messages = _Messages(runtime_client)
