import io
import json
from unittest.mock import MagicMock, patch

from library.assistant import runtime

ENV = {
    "AWS_REGION": "us-east-2", "SPORT_REGISTRY_TABLE_NAME": "registry", "ASSISTANT_TABLE_NAME": "assistant",
    "GUARDRAIL_ID": "g", "GUARDRAIL_VERSION": "1", "MEMORY_ID": "m", "ASSISTANT_PROFILE": "admin",
    "PUBLIC_TOOLS_FUNCTION_NAME": "public-tools", "ADMIN_TOOLS_FUNCTION_NAME": "admin-tools", "GUARDRAIL_GROUNDING": "true",
}


class FakeLambda:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or {"Payload": io.BytesIO(b'{"ok": true, "result": {}}')}

    def invoke(self, FunctionName, Payload):
        self.calls.append((FunctionName, json.loads(Payload)))
        return self.response


def test_tools_go_to_the_public_lambda_unless_they_are_admin_tools():
    client = FakeLambda()
    invoke = runtime.tool_invoker(client, ENV)

    assert invoke("list_events", {"sport": "nfl"}) == {"ok": True, "result": {}}
    client.response = {"Payload": io.BytesIO(b"{}")}
    invoke("get_cost_summary", {})

    assert [name for name, _ in client.calls] == ["public-tools", "admin-tools"]
    assert client.calls[0][1] == {"tool": "list_events", "input": {"sport": "nfl"}}


def test_a_tool_lambda_crash_becomes_a_message():
    client = FakeLambda({"FunctionError": "Unhandled", "Payload": io.BytesIO(b"{}")})
    assert runtime.tool_invoker(client, ENV)("list_events", {})["ok"] is False


def test_clients_and_profile_come_from_the_environment():
    with patch.object(runtime.boto3, "client", MagicMock()), patch.object(runtime.boto3, "resource", MagicMock()), \
            patch.object(runtime, "load_sports", return_value=["nfl", "nba"]):
        clients = runtime.build_clients(ENV, model="model")
        profile = runtime.profile_for(ENV, clients)
        sports = clients.sports()

    assert clients.model == "model"
    assert clients.memory_id == "m"
    assert clients.grounding is True
    assert sports == ["nfl", "nba"]
    assert profile.name == "admin"
    assert "nfl, nba" in profile.system


def test_claude_uses_the_anthropic_client_and_other_models_use_converse():
    claude = MagicMock(name="anthropic-client")

    with patch.object(runtime.boto3, "client") as boto_client:
        assert runtime.model_client({**ENV, "ASSISTANT_MODEL_ID": "us.anthropic.claude-haiku-5-5"}, lambda: claude) is claude
        assert runtime.model_client({**ENV, "ASSISTANT_MODEL_ID": "anthropic.claude-haiku-4-5"}, lambda: claude) is claude
        other = runtime.model_client({**ENV, "ASSISTANT_MODEL_ID": "openai.gpt-oss-120b-1:0"}, lambda: claude)

    assert type(other).__name__ == "ConverseModel"
    assert boto_client.call_args.args == ("bedrock-runtime",)


class Throttled(Exception):
    pass


class ThrottledLambda(FakeLambda):
    """Throttles the first `throttles` invokes, like a Lambda at its reserved concurrency."""
    exceptions = type("Exceptions", (), {"TooManyRequestsException": Throttled})

    def __init__(self, throttles):
        super().__init__()
        self.throttles = throttles

    def invoke(self, FunctionName, Payload):
        if self.throttles:
            self.throttles -= 1
            raise Throttled()
        return super().invoke(FunctionName, Payload)


def test_a_throttled_tool_is_retried_once_then_reported_busy(monkeypatch):
    monkeypatch.setattr(runtime, "THROTTLE_RETRY_SECONDS", 0)
    assert runtime.tool_invoker(ThrottledLambda(1), ENV)("list_events", {})["ok"] is True
    busy = runtime.tool_invoker(ThrottledLambda(2), ENV)("list_events", {})
    assert busy == {"ok": False, "error": runtime.BUSY}
