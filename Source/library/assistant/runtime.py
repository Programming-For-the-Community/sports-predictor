"""
Builds an agent's clients and profile from its environment variables -- the
same values Terraform gives each AgentCore runtime. Used by the runtime
itself (Source/agents/assistant/agent.py) and by the eval runner, which reads
a deployed runtime's variables so it tests exactly what is live.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping

import boto3
from botocore.config import Config

from library.assistant import agent_core
from library.assistant.converse_model import ConverseModel
from library.assistant.prompts import Profile, build_profile
from library.assistant.settings import AgentSettings
from library.assistant.sports import load_sports
from library.assistant.tool_specs import ADMIN_TOOL_NAMES

logger = logging.getLogger(__name__)

DEFAULT_TEMPERATURE = 0.2


def tool_invoker(lambda_client, env: Mapping[str, str]):
    """invoke_tool for agent_core: admin tools go to the admin tool Lambda,
    everything else to the public one."""
    def invoke_tool(name: str, args: dict) -> dict:
        function = env["ADMIN_TOOLS_FUNCTION_NAME"] if name in ADMIN_TOOL_NAMES else env["PUBLIC_TOOLS_FUNCTION_NAME"]
        response = lambda_client.invoke(FunctionName=function, Payload=json.dumps({"tool": name, "input": args}).encode())
        if response.get("FunctionError"):
            logger.error("Tool %s raised in %s", name, function)
            return {"ok": False, "error": "The tool failed. Try again shortly."}
        return json.loads(response["Payload"].read() or b"{}")
    return invoke_tool


def is_claude(model_id: str) -> bool:
    """anthropic.claude-... or an inference profile of one (us.anthropic...)."""
    return model_id.startswith("anthropic.") or ".anthropic." in model_id


def model_client(env: Mapping[str, str], claude_client: Callable[[], object]):
    """The client for ASSISTANT_MODEL_ID: claude_client() (an AnthropicBedrock,
    built by the caller so this module doesn't need the Anthropic SDK) for
    Claude, the Converse adapter for any other Bedrock model."""
    if is_claude(env["ASSISTANT_MODEL_ID"]):
        return claude_client()
    # A model call with several tool results in context can take a while.
    config = Config(read_timeout=120, retries={"max_attempts": 3, "mode": "standard"})
    # Low by default: answers come from tool data, not invention.
    temperature = float(env.get("ASSISTANT_TEMPERATURE") or DEFAULT_TEMPERATURE)
    return ConverseModel(boto3.client("bedrock-runtime", region_name=env["AWS_REGION"], config=config), temperature)


def build_clients(env: Mapping[str, str], model) -> agent_core.Clients:
    """model: from model_client."""
    region = env["AWS_REGION"]
    dynamodb = boto3.resource("dynamodb", region_name=region)
    registry = dynamodb.Table(env["SPORT_REGISTRY_TABLE_NAME"])
    return agent_core.Clients(
        model=model,
        guardrails=boto3.client("bedrock-runtime", region_name=region),
        memory=boto3.client("bedrock-agentcore", region_name=region),
        table=dynamodb.Table(env["ASSISTANT_TABLE_NAME"]),
        invoke_tool=tool_invoker(boto3.client("lambda", region_name=region), env),
        sports=lambda: load_sports(registry),
        guardrail_id=env["GUARDRAIL_ID"],
        guardrail_version=env["GUARDRAIL_VERSION"],
        memory_id=env["MEMORY_ID"],
        grounding=env.get("GUARDRAIL_GROUNDING") == "true",
    )


def profile_for(env: Mapping[str, str], clients: agent_core.Clients, settings: AgentSettings | None = None) -> Profile:
    return build_profile(env["ASSISTANT_PROFILE"], settings or AgentSettings.from_env(), clients.sports())
