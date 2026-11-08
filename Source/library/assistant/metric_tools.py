"""
The admin agent's CloudWatch-metric tools: API Gateway traffic and Lambda
health. One datapoint per metric covers the whole requested range.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from library.assistant.date_ranges import requested_range, today
from library.assistant.settings import ToolSettings
from library.assistant.traffic_tools import traffic_rows

# CloudWatch keeps metrics for 15 months.
MAX_METRIC_DAYS = 455
TOP_FUNCTIONS = 10
NEAR_TIMEOUT = 0.95
METRIC_BATCH = 500
API_GATEWAY = "AWS/ApiGateway"
LAMBDA = "AWS/Lambda"


def _window(args: dict) -> tuple[date, date, datetime, datetime, int]:
    start, end = requested_range(args, 7)
    oldest = today() - timedelta(days=MAX_METRIC_DAYS)
    start = max(start, oldest)
    begin = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    finish = min(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc), datetime.now(timezone.utc))
    if begin >= finish:
        raise ValueError(f"No metric history for that range: CloudWatch keeps the last {MAX_METRIC_DAYS} days, from {oldest.isoformat()}.")
    period = max(60, int((finish - begin).total_seconds()) // 60 * 60)
    return start, end, begin, finish, period


def _values(cloudwatch, queries: list[dict], begin: datetime, finish: datetime) -> dict[str, float]:
    values: dict[str, float] = {}
    for i in range(0, len(queries), METRIC_BATCH):
        response = cloudwatch.get_metric_data(MetricDataQueries=queries[i:i + METRIC_BATCH], StartTime=begin, EndTime=finish)
        for result in response["MetricDataResults"]:
            values[result["Id"]] = sum(result["Values"]) if result["Values"] else 0.0
    return values


def _query(qid: str, namespace: str, metric: str, dimension: tuple[str, str], stat: str, period: int) -> dict:
    return {"Id": qid, "MetricStat": {"Metric": {"Namespace": namespace, "MetricName": metric,
            "Dimensions": [{"Name": dimension[0], "Value": dimension[1]}]}, "Period": period, "Stat": stat}}


def api_traffic(s3, args: dict, settings: ToolSettings, cloudwatch, api_name: str) -> dict:
    start, end, begin, finish, period = _window(args)
    api = ("ApiName", api_name)
    values = _values(cloudwatch, [
        _query("requests", API_GATEWAY, "Count", api, "Sum", period),
        _query("e4xx", API_GATEWAY, "4XXError", api, "Sum", period),
        _query("e5xx", API_GATEWAY, "5XXError", api, "Sum", period),
        _query("p95", API_GATEWAY, "Latency", api, "p95", period),
    ], begin, finish)
    rows, _ = traffic_rows(s3, start, end, "api_gateway")
    by_route: dict[tuple, int] = defaultdict(int)
    for _, row in rows:
        by_route[(row["rule"], row["method"], row["path"])] += row["n"]
    denied = [{"status": k[0], "method": k[1], "route": k[2], "count": n} for k, n in sorted(by_route.items(), key=lambda kv: -kv[1])]
    return {
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "requests": int(values.get("requests", 0)), "errors_4xx": int(values.get("e4xx", 0)),
        "errors_5xx": int(values.get("e5xx", 0)), "latency_p95_ms": round(values.get("p95", 0.0), 1),
        "denied_by_route": denied[:settings.max_rows],
        "note": f"Metric history is kept for {MAX_METRIC_DAYS} days; older days are left out.",
    }


def _project_functions(lambda_client, project: str) -> list[dict]:
    functions: list[dict] = []
    for page in lambda_client.get_paginator("list_functions").paginate():
        functions.extend(f for f in page["Functions"] if f["FunctionName"].startswith(f"{project}-"))
    return functions


def _health_row(fn: dict, index: int, values: dict[str, float], project: str) -> dict | None:
    invocations = int(values.get(f"m{index}_invocations", 0))
    errors = int(values.get(f"m{index}_errors", 0))
    throttles = int(values.get(f"m{index}_throttles", 0))
    max_ms = values.get(f"m{index}_duration", 0.0)
    near_timeout = max_ms >= NEAR_TIMEOUT * fn.get("Timeout", 3) * 1000
    if not (errors or throttles or near_timeout):
        return None
    return {
        "function": fn["FunctionName"].removeprefix(f"{project}-"), "invocations": invocations, "errors": errors,
        "error_pct": round(100 * errors / invocations, 2) if invocations else None, "throttles": throttles,
        "max_duration_s": round(max_ms / 1000, 1), "timeout_s": fn.get("Timeout"), "hit_timeout": near_timeout,
    }


def lambda_health(args: dict, cloudwatch, lambda_client, project: str) -> dict:
    start, end, begin, finish, period = _window(args)
    functions = _project_functions(lambda_client, project)
    queries = [
        _query(f"m{i}_{metric.lower()}", LAMBDA, metric, ("FunctionName", fn["FunctionName"]), stat, period)
        for i, fn in enumerate(functions)
        for metric, stat in (("Invocations", "Sum"), ("Errors", "Sum"), ("Throttles", "Sum"), ("Duration", "Maximum"))
    ]
    values = _values(cloudwatch, queries, begin, finish) if queries else {}
    rows = [r for i, fn in enumerate(functions) if (r := _health_row(fn, i, values, project))]
    rows.sort(key=lambda r: (-(r["errors"] + r["throttles"]), -r["max_duration_s"]))
    return {"start_date": start.isoformat(), "end_date": end.isoformat(), "functions_checked": len(functions),
            "with_problems": len(rows), "functions": rows[:TOP_FUNCTIONS]}
