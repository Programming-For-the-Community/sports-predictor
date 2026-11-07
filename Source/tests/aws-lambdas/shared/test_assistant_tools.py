"""
assistant-public-tools and assistant-admin-tools handler wiring -- the tools
themselves are covered by tests/library/assistant/.
"""
import io
import json
import sys
from unittest.mock import patch

public_handler = sys.modules["shared_assistant_public_tools"]
admin_handler = sys.modules["shared_assistant_admin_tools"]


class FakeLambdaClient:
    def __init__(self, response):
        self.response = response
        self.invoked = []

    def invoke(self, FunctionName, Payload):
        self.invoked.append((FunctionName, json.loads(Payload)))
        return {"Payload": io.BytesIO(json.dumps(self.response).encode())}


def test_public_tools_read_through_predict_read_with_an_api_gateway_shaped_request(monkeypatch):
    monkeypatch.setenv("PREDICT_READ_FUNCTION_NAME", "sports-predictor-predict-read")
    client = FakeLambdaClient({"statusCode": 200, "body": json.dumps({"sport": "nfl", "events": [{"event_id": "1"}]})})

    with patch.object(public_handler, "_lambda", client), patch.object(public_handler, "load_sports", return_value=["nfl"]):
        response = public_handler.lambda_handler({"tool": "list_events", "input": {"sport": "nfl", "when": "recent"}}, None)

    assert response["ok"] is True
    assert response["result"]["count"] == 1
    name, request = client.invoked[0]
    assert name == "sports-predictor-predict-read"
    assert request == {"resource": "/nfl/events", "httpMethod": "GET", "pathParameters": {}, "queryStringParameters": {"status": "completed"}}


def test_public_tools_refuse_an_admin_tool_and_bad_input():
    with patch.object(public_handler, "load_sports", return_value=["nfl"]):
        _refuses_admin_and_bad_input()


def _refuses_admin_and_bad_input():
    assert public_handler.lambda_handler({"tool": "get_cost_summary", "input": {}}, None)["ok"] is False
    assert public_handler.lambda_handler({"tool": "list_events", "input": {"sport": "nfl"}}, None)["ok"] is False


def test_a_tool_crash_returns_a_message_not_an_exception(monkeypatch):
    monkeypatch.setenv("PREDICT_READ_FUNCTION_NAME", "x")

    class Broken:
        def invoke(self, **kwargs):
            raise RuntimeError("boom")

    with patch.object(public_handler, "_lambda", Broken()), patch.object(public_handler, "load_sports", return_value=["nba"]):
        response = public_handler.lambda_handler({"tool": "get_season_projection", "input": {"sport": "nba"}}, None)

    assert response == {"ok": False, "error": "The tool failed. Try again shortly."}


def test_admin_tools_pass_their_config_through(monkeypatch):
    monkeypatch.setenv("API_GATEWAY_NAME", "sports-predictor-api")
    monkeypatch.setenv("PROJECT_NAME", "sports-predictor")
    seen = {}

    def run(name, args, tools_context):
        seen.update(name=name, args=args, api_name=tools_context.api_name, project=tools_context.project)
        return {"total": 1}

    with patch.object(admin_handler.admin_tools, "run", run), patch.object(admin_handler, "_get_s3", return_value="s3"):
        response = admin_handler.lambda_handler({"tool": "get_cost_summary", "input": {"group_by": "sport"}}, None)

    assert response == {"ok": True, "result": {"total": 1}}
    assert seen == {"name": "get_cost_summary", "args": {"group_by": "sport"}, "api_name": "sports-predictor-api", "project": "sports-predictor"}


def test_admin_tools_refuse_a_public_tool_name():
    with patch.object(admin_handler, "_get_s3", return_value="s3"):
        assert admin_handler.lambda_handler({"tool": "list_events", "input": {"sport": "nfl", "when": "recent"}}, None)["ok"] is False
