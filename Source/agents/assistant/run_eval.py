"""
Runs the assistant eval (library.assistant.eval_cases) against the deployed
agents: real Claude Haiku 5.5, guardrails, memory and tool Lambdas, with the
settings read from each live AgentCore runtime. Only the runtime's JWT
authorizer is skipped -- the turn runs in this process under the eval's own
caller id, with a raised daily limit so the run isn't cut off by the quota.

Run inside the agent image so the SDK versions match the runtime:

  docker run --rm -v <repo>/Source:/src -w /src -v <home>/.aws:/home/agent/.aws:ro -e AWS_PROFILE=<profile> \\
    -e PYTHONPATH=/src:/app/packages assistant-agent:local \\
    python agents/assistant/run_eval.py --public-runtime <id> --admin-runtime <id> --out eval-results.json

--dry-run lists the cases and estimates the cost without calling anything.
"""
import argparse
import base64
import json
import os
import secrets
import sys
from dataclasses import replace

import boto3
from anthropic import AnthropicBedrock

from library.assistant import agent_core, runtime
from library.assistant.agent_core import Clients
from library.assistant.eval_cases import build_cases
from library.assistant.eval_grading import grade, summarize
from library.assistant.settings import AgentSettings
from library.assistant.sports import load_sports

EVAL_SUB = "assistant-eval-runner"
EVAL_DAILY_LIMIT = 1000
# Per question: ~3 model calls (~24K input, ~800 output tokens at Haiku 5.5's
# $0.10/$0.50 per million) plus two guardrail checks (~4 text units).
EST_USD_PER_QUESTION = 0.0024 + 0.0004 + 0.0012


def _token(admin: bool) -> str:
    claims = {"sub": EVAL_SUB, "cognito:groups": ["admins"] if admin else []}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"Bearer eval.{body}.unsigned"


def _runtime_env(control, runtime_id: str) -> dict:
    env = dict(control.get_agent_runtime(agentRuntimeId=runtime_id)["environmentVariables"])
    env.setdefault("AWS_REGION", control.meta.region_name)
    return env


def _agent(control, runtime_id: str) -> tuple[Clients, object]:
    env = _runtime_env(control, runtime_id)
    clients = runtime.build_clients(env, AnthropicBedrock(aws_region=env["AWS_REGION"]))
    settings = replace(
        AgentSettings.from_env(),
        model_id=env["ASSISTANT_MODEL_ID"], effort=env["ASSISTANT_EFFORT"], daily_limit=EVAL_DAILY_LIMIT,
    )
    return clients, runtime.profile_for(env, clients, settings)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-runtime", required=True)
    parser.add_argument("--admin-runtime", required=True)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-2"))
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--out", default="eval-results.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    control = boto3.client("bedrock-agentcore-control", region_name=args.region)
    registry = _runtime_env(control, args.public_runtime)["SPORT_REGISTRY_TABLE_NAME"]
    cases = build_cases(load_sports(boto3.resource("dynamodb", region_name=args.region).Table(registry)))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.dry_run:
        print(json.dumps({"cases": len(cases), "estimated_usd": round(len(cases) * EST_USD_PER_QUESTION, 2),
                          "ids": [c["id"] for c in cases]}, indent=2))
        return 0

    agents = {"public": _agent(control, args.public_runtime), "admin": _agent(control, args.admin_runtime)}
    results = []
    for case in cases:
        clients, profile = agents[case["agent"]]
        payload = {"message": case["question"], **case.get("context", {})}
        events = list(agent_core.handle(profile, clients, payload, _token(case["agent"] == "admin"), secrets.token_hex(32)))
        result = grade(case, events)
        results.append(result)
        print(("PASS " if result["passed"] else "FAIL ") + case["id"] + ("" if result["passed"] else f"  {result['reasons']}"), flush=True)

    summary = summarize(results)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "results": results}, handle, indent=2)
    print(json.dumps({k: summary[k] for k in ("cases", "passed", "pass_rate", "by_agent")}, indent=2))
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
