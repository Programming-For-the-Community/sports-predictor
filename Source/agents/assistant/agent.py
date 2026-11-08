"""
The assistant agent, served by Amazon Bedrock AgentCore Runtime. One image
runs both agents: ASSISTANT_PROFILE (public | admin) picks the system
prompt, tools and daily limit, and Terraform gives each runtime its own
role, authorizer, guardrail, memory and settings (Terraform/agentcore-
assistant.tf). Every limit is an environment variable (library.assistant.
settings); the sports come from the sport registry table. Clients are built
by library.assistant.runtime, which the eval runner shares.

The turn itself -- quota, guardrails, memory, the Claude Haiku 5.5 tool
loop -- is library.assistant.agent_core. Each yielded event goes to the app
as one SSE `data:` line.

The runtime's JWT authorizer has verified the caller's Cognito ID token
before a request arrives; it forwards the Authorization header so the
turn can read the caller's sub (and, for admin, groups) from it.
"""
import logging
import os

from anthropic import AnthropicBedrock
from bedrock_agentcore import BedrockAgentCoreApp

from library.assistant import agent_core, gen_ai_spans, runtime, turn_log

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

app = BedrockAgentCoreApp()
clients = runtime.build_clients(os.environ, AnthropicBedrock(aws_region=os.environ["AWS_REGION"]))
# GenAI spans for AgentCore Evaluations (library.assistant.gen_ai_spans).
clients.tracer = gen_ai_spans.otel_tracer()


@app.entrypoint
def invoke(payload, context):
    headers = context.request_headers or {}
    authorization = headers.get("Authorization")
    profile = runtime.profile_for(os.environ, clients)
    for event in agent_core.handle(profile, clients, payload or {}, authorization, context.session_id):
        if event["type"] in turn_log.TERMINAL:
            sub = agent_core.token_claims(authorization).get("sub")
            turn_logger.info(turn_log.record(PROFILE_NAME, sub, context.session_id, payload or {}, event))
        yield event


if __name__ == "__main__":
    app.run()
