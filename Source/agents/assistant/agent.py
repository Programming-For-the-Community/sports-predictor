"""
The assistant agent, served by Amazon Bedrock AgentCore Runtime. One image
runs both agents: ASSISTANT_PROFILE (public | admin) picks the system
prompt, tools and daily limit, and Terraform gives each runtime its own
role, authorizer, guardrail, memory and settings (Terraform/agentcore-
assistant.tf). Every limit is an environment variable (library.assistant.
settings); the sports come from the sport registry table.

The turn itself -- quota, guardrails, memory, the Claude Haiku 5.5 tool
loop -- is library.assistant.agent_core; this file only builds the clients.
Each yielded event goes to the app as one SSE `data:` line.

The runtime's JWT authorizer has verified the caller's Cognito ID token
before a request arrives; it forwards the Authorization header so the
turn can read the caller's sub (and, for admin, groups) from it.
"""
import json
import logging
import os

import boto3
from anthropic import AnthropicBedrockMantle
from bedrock_agentcore import BedrockAgentCoreApp

from library.assistant import agent_core, turn_log
from library.assistant.prompts import build_profile
from library.assistant.settings import AgentSettings
from library.assistant.sports import load_sports
from library.assistant.tool_specs import ADMIN_TOOL_NAMES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
logger = logging.getLogger("assistant-agent")

# Turn lines are bare JSON, one per line, so the log group's metric filters
# (Terraform/cloudwatch-alarms-assistant.tf) can read their fields.
turn_logger = logging.getLogger("assistant-turns")
turn_logger.propagate = False
_turn_handler = logging.StreamHandler()
_turn_handler.setFormatter(logging.Formatter("%(message)s"))
turn_logger.addHandler(_turn_handler)
turn_logger.setLevel(logging.INFO)

PROFILE_NAME = os.environ["ASSISTANT_PROFILE"]
REGION = os.environ["AWS_REGION"]
SETTINGS = AgentSettings.from_env()

app = BedrockAgentCoreApp()
_lambda = boto3.client("lambda", region_name=REGION)
_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_registry = _dynamodb.Table(os.environ["SPORT_REGISTRY_TABLE_NAME"])


def sports() -> list[str]:
    return load_sports(_registry)


def invoke_tool(name: str, args: dict) -> dict:
    function = os.environ["ADMIN_TOOLS_FUNCTION_NAME"] if name in ADMIN_TOOL_NAMES else os.environ["PUBLIC_TOOLS_FUNCTION_NAME"]
    response = _lambda.invoke(FunctionName=function, Payload=json.dumps({"tool": name, "input": args}).encode())
    if response.get("FunctionError"):
        logger.error("Tool %s raised in %s", name, function)
        return {"ok": False, "error": "The tool failed. Try again shortly."}
    return json.loads(response["Payload"].read() or b"{}")


clients = agent_core.Clients(
    model=AnthropicBedrockMantle(aws_region=REGION),
    guardrails=boto3.client("bedrock-runtime", region_name=REGION),
    memory=boto3.client("bedrock-agentcore", region_name=REGION),
    table=_dynamodb.Table(os.environ["ASSISTANT_TABLE_NAME"]),
    invoke_tool=invoke_tool,
    sports=sports,
    guardrail_id=os.environ["GUARDRAIL_ID"],
    guardrail_version=os.environ["GUARDRAIL_VERSION"],
    memory_id=os.environ["MEMORY_ID"],
    grounding=os.environ.get("GUARDRAIL_GROUNDING") == "true",
)


@app.entrypoint
def invoke(payload, context):
    headers = context.request_headers or {}
    authorization = headers.get("Authorization")
    profile = build_profile(PROFILE_NAME, SETTINGS, sports())
    for event in agent_core.handle(profile, clients, payload or {}, authorization, context.session_id):
        if event["type"] in turn_log.TERMINAL:
            sub = agent_core.token_claims(authorization).get("sub")
            turn_logger.info(turn_log.record(PROFILE_NAME, sub, context.session_id, payload or {}, event))
        yield event


if __name__ == "__main__":
    app.run()
