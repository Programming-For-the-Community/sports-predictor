"""
Runs the assistant eval (library.assistant.eval_cases) against the deployed
agents: the real Claude model, guardrails, memory and tool Lambdas, with the
settings read from each live AgentCore runtime. Only the runtime's JWT
authorizer is skipped -- the turn runs in this process under the eval's own
caller id, with a raised daily limit so the run isn't cut off by the quota.

Run inside the agent image so the SDK versions match the runtime:

  docker run --rm -v <repo>/Source:/src -w /src -v <home>/.aws:/home/agent/.aws:ro -e AWS_PROFILE=<profile> \\
    -e PYTHONPATH=/src:/app/packages assistant-agent:local \\
    python agents/assistant/run_eval.py --public-runtime <id> --admin-runtime <id>

Results are written to eval-results.json in the working directory (-w).
--model <bedrock model id> tries another model with the same runtimes'
settings, no deploy needed; --sample N runs N cases spread across the set.

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
from library.assistant.eval_grading import estimate_usd, grade, summarize
from library.assistant.settings import AgentSettings
from library.assistant.sports import load_sports

EVAL_SUB = "assistant-eval-runner"
EVAL_DAILY_LIMIT = 1000
# Written to the working directory.
RESULTS_FILE = "eval-results.json"


def _token(admin: bool) -> str:
    claims = {"sub": EVAL_SUB, "cognito:groups": ["admins"] if admin else []}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"Bearer eval.{body}.unsigned"


def _runtime_env(control, runtime_id: str, overrides: dict) -> dict:
    env = dict(control.get_agent_runtime(agentRuntimeId=runtime_id)["environmentVariables"])
    env.setdefault("AWS_REGION", control.meta.region_name)
    return {**env, **overrides}


def _overrides(args) -> dict:
    """--model tries another model without a deploy; effort is left out
    unless --effort is given, since most models have none."""
    if not args.model:
        return {}
    return {"ASSISTANT_MODEL_ID": args.model, "ASSISTANT_EFFORT": args.effort or ""}


def _sample(cases: list[dict], count: int | None) -> list[dict]:
    """count cases spread evenly across the set (both agents, every kind)."""
    if not count or count >= len(cases):
        return cases
    return [cases[i * len(cases) // count] for i in range(count)]


def _agent(control, runtime_id: str, overrides: dict) -> tuple[Clients, object]:
    env = _runtime_env(control, runtime_id, overrides)
    clients = runtime.build_clients(env, runtime.model_client(env, lambda: AnthropicBedrock(aws_region=env["AWS_REGION"])))
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
    parser.add_argument("--sample", type=int, help="run this many cases, spread across the set")
    parser.add_argument("--model", help="Bedrock model ID to evaluate instead of the runtime's")
    parser.add_argument("--effort", help="effort for --model (Claude only)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    control = boto3.client("bedrock-agentcore-control", region_name=args.region)
    overrides = _overrides(args)
    public_env = _runtime_env(control, args.public_runtime, overrides)
    registry = boto3.resource("dynamodb", region_name=args.region).Table(public_env["SPORT_REGISTRY_TABLE_NAME"])
    cases = build_cases(load_sports(registry))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    cases = _sample(cases, args.sample)
    if args.dry_run:
        print(json.dumps({"cases": len(cases), "model": public_env["ASSISTANT_MODEL_ID"],
                          "estimated_usd": estimate_usd(public_env["ASSISTANT_MODEL_ID"], len(cases)),
                          "ids": [c["id"] for c in cases]}, indent=2))
        return 0

    agents = {"public": _agent(control, args.public_runtime, overrides), "admin": _agent(control, args.admin_runtime, overrides)}
    results = []
    for case in cases:
        clients, profile = agents[case["agent"]]
        payload = {"message": case["question"], **case.get("context", {})}
        events = list(agent_core.handle(profile, clients, payload, _token(case["agent"] == "admin"), secrets.token_hex(32)))
        result = grade(case, events)
        results.append(result)
        print(("PASS " if result["passed"] else "FAIL ") + case["id"] + ("" if result["passed"] else f"  {result['reasons']}"), flush=True)

    summary = summarize(results)
    with open(RESULTS_FILE, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "results": results}, handle, indent=2)
    print(json.dumps({"model": public_env["ASSISTANT_MODEL_ID"], **{k: summary[k] for k in ("cases", "passed", "pass_rate", "by_agent")}}, indent=2))
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
