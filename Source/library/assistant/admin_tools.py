"""
The admin agent's read tools, run by aws-lambdas/shared/assistant-admin-
tools. Cost and denied-traffic answers come only from the S3 aggregates the
two producers write; API and Lambda health come from CloudWatch metrics.
Any date range is accepted and clipped to the data that exists -- every
answer says where its data starts. Pipeline health reads the events table;
assistant usage reads the agents' daily rows in the assistant table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from library.assistant import cost_tools, metric_tools, pipeline_health, traffic_tools, training_runs, usage
from library.assistant.date_ranges import eastern_today, requested_range
from library.assistant.settings import ToolSettings
from library.assistant.tool_specs import ADMIN_TOOL_NAMES, validate


@dataclass
class AdminContext:
    s3: object
    settings: ToolSettings
    cloudwatch: object = None
    lambda_client: object = None
    api_name: str = ""
    project: str = ""
    events_table: object = None
    assistant_table: object = None
    sports: Callable[[], list[str]] = field(default=list)
    today: Callable[[], object] = field(default=eastern_today)
    stepfunctions: object = None
    training_state_machine_arn: str = ""


def _pipeline(ctx: AdminContext, args: dict) -> dict:
    sports = [args["sport"]] if args.get("sport") else ctx.sports()
    return pipeline_health.pipeline_health(ctx.events_table, ctx.cloudwatch, ctx.project, sports, ctx.today(), datetime.now(timezone.utc))


def _training(ctx: AdminContext, args: dict) -> dict:
    start, end = requested_range(args, 30)
    return training_runs.report(ctx.stepfunctions, ctx.training_state_machine_arn, start, end, args.get("sport"))


def _usage(ctx: AdminContext, args: dict) -> dict:
    start, end = requested_range(args, 7)
    return usage.report(ctx.assistant_table, args, start, end, ctx.settings.max_rows)


_TOOLS = {
    "get_cost_summary": lambda ctx, args: cost_tools.cost_summary(ctx.s3, args),
    "get_denied_locations": lambda ctx, args: traffic_tools.denied_locations(ctx.s3, args, ctx.settings),
    "get_repeat_offenders": lambda ctx, args: traffic_tools.repeat_offenders(ctx.s3, args, ctx.settings),
    "get_denied_endpoints": lambda ctx, args: traffic_tools.denied_endpoints(ctx.s3, args, ctx.settings),
    "get_denied_trend": lambda ctx, args: traffic_tools.denied_trend(ctx.s3, args, ctx.settings),
    "get_api_traffic": lambda ctx, args: metric_tools.api_traffic(ctx.s3, args, ctx.settings, ctx.cloudwatch, ctx.api_name),
    "get_lambda_health": lambda ctx, args: metric_tools.lambda_health(args, ctx.cloudwatch, ctx.lambda_client, ctx.project),
    "get_pipeline_health": _pipeline,
    "get_assistant_usage": _usage,
    "get_training_runs": _training,
}


def run(name: str, args: dict | None, ctx: AdminContext) -> dict:
    if name not in ADMIN_TOOL_NAMES:
        raise ValueError(f"{name!r} is not an admin tool")
    return _TOOLS[name](ctx, validate(name, args, ctx.sports()))
