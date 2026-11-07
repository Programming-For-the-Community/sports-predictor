"""
The admin agent's read tools, run by aws-lambdas/shared/assistant-admin-
tools. Cost and denied-traffic answers come only from the S3 aggregates the
two producers write; API and Lambda health come from CloudWatch metrics.
Any date range is accepted and clipped to the data that exists -- every
answer says where its data starts.
"""
from __future__ import annotations

from dataclasses import dataclass

from library.assistant import cost_tools, metric_tools, traffic_tools
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


_TOOLS = {
    "get_cost_summary": lambda ctx, args: cost_tools.cost_summary(ctx.s3, args),
    "get_denied_locations": lambda ctx, args: traffic_tools.denied_locations(ctx.s3, args, ctx.settings),
    "get_repeat_offenders": lambda ctx, args: traffic_tools.repeat_offenders(ctx.s3, args, ctx.settings),
    "get_denied_endpoints": lambda ctx, args: traffic_tools.denied_endpoints(ctx.s3, args, ctx.settings),
    "get_denied_trend": lambda ctx, args: traffic_tools.denied_trend(ctx.s3, args, ctx.settings),
    "get_api_traffic": lambda ctx, args: metric_tools.api_traffic(ctx.s3, args, ctx.settings, ctx.cloudwatch, ctx.api_name),
    "get_lambda_health": lambda ctx, args: metric_tools.lambda_health(args, ctx.cloudwatch, ctx.lambda_client, ctx.project),
}


def run(name: str, args: dict | None, ctx: AdminContext) -> dict:
    if name not in ADMIN_TOOL_NAMES:
        raise ValueError(f"{name!r} is not an admin tool")
    # Admin tools take no sport argument, so no sports list is needed to validate them.
    return _TOOLS[name](ctx, validate(name, args, []))
