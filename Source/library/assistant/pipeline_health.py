"""
The admin agent's data-pipeline check, per sport: the newest finished game
and the next scheduled one in the events table, games still "scheduled" two
or more days after their date (a missed result or a game ESPN dropped), and
each pipeline Lambda's runs and errors over the last two days.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key

STAGES = ("ingest", "normalize", "predict", "live-scores")
LOOKBACK_HOURS = 48
OVERDUE_DAYS = 2
OVERDUE_SHOWN = 10
STATUS_INDEX = "sport-status-index"


def _query(table, condition, forward: bool, limit: int | None = None) -> list[dict]:
    kwargs = {"IndexName": STATUS_INDEX, "KeyConditionExpression": condition, "ScanIndexForward": forward}
    if limit:
        kwargs["Limit"] = limit
    return table.query(**kwargs).get("Items", [])


def _count(table, condition) -> int:
    kwargs = {"IndexName": STATUS_INDEX, "KeyConditionExpression": condition, "Select": "COUNT"}
    total = 0
    while True:
        page = table.query(**kwargs)
        total += page.get("Count", 0)
        if "LastEvaluatedKey" not in page:
            return total
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _game(item: dict | None) -> dict | None:
    if not item:
        return None
    return {"event_id": item.get("event_id"), "event_date": item.get("event_date"), "kickoff_time": item.get("kickoff_time")}


def games(table, sport: str, today: date) -> dict:
    status = Key("sport_status")
    latest = _query(table, status.eq(f"{sport}#completed"), forward=False, limit=1)
    upcoming = _query(table, status.eq(f"{sport}#scheduled") & Key("event_date").gte(today.isoformat()), forward=True, limit=1)
    overdue_condition = status.eq(f"{sport}#scheduled") & Key("event_date").lte((today - timedelta(days=OVERDUE_DAYS)).isoformat())
    overdue = _count(table, overdue_condition)
    result = {"last_finished_game": _game(latest[0] if latest else None), "next_scheduled_game": _game(upcoming[0] if upcoming else None),
              "overdue_scheduled": overdue}
    if overdue:
        result["overdue_examples"] = [_game(i) for i in _query(table, overdue_condition, forward=False, limit=OVERDUE_SHOWN)]
    return result


def _metric(qid: str, function: str, metric: str) -> dict:
    return {"Id": qid, "MetricStat": {"Metric": {"Namespace": "AWS/Lambda", "MetricName": metric,
            "Dimensions": [{"Name": "FunctionName", "Value": function}]}, "Period": 3600, "Stat": "Sum"}}


def lambda_runs(cloudwatch, project: str, sports: list[str], now: datetime) -> dict[str, list[dict]]:
    """sport -> one row per pipeline stage: runs and errors in the lookback,
    and the hour of the last run."""
    jobs = [(sport, stage, f"q{i}") for i, (sport, stage) in enumerate((s, st) for s in sports for st in STAGES)]
    queries = [q for sport, stage, qid in jobs
               for q in (_metric(f"{qid}r", f"{project}-{sport}-{stage}", "Invocations"), _metric(f"{qid}e", f"{project}-{sport}-{stage}", "Errors"))]
    results: dict[str, dict] = {}
    for i in range(0, len(queries), 500):
        response = cloudwatch.get_metric_data(MetricDataQueries=queries[i:i + 500], StartTime=now - timedelta(hours=LOOKBACK_HOURS), EndTime=now)
        results.update({r["Id"]: r for r in response["MetricDataResults"]})
    by_sport: dict[str, list[dict]] = {sport: [] for sport in sports}
    for sport, stage, qid in jobs:
        runs, errors = results.get(f"{qid}r", {}), results.get(f"{qid}e", {})
        active = [t for t, v in zip(runs.get("Timestamps", []), runs.get("Values", [])) if v]
        by_sport[sport].append({"stage": stage, "runs": int(sum(runs.get("Values", []))), "errors": int(sum(errors.get("Values", []))),
                                "last_run_hour_utc": max(active).astimezone(timezone.utc).strftime("%Y-%m-%d %H:00") if active else None})
    return by_sport


def pipeline_health(events_table, cloudwatch, project: str, sports: list[str], today: date, now: datetime) -> dict:
    with ThreadPoolExecutor(max_workers=max(1, min(len(sports), 8))) as pool:
        per_sport = dict(zip(sports, pool.map(lambda sport: games(events_table, sport, today), sports)))
    runs = lambda_runs(cloudwatch, project, sports, now)
    return {
        "checked_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "sports": [{"sport": sport, **per_sport[sport], "lambdas": runs[sport]} for sport in sports],
        "note": (f"overdue_scheduled counts games still scheduled {OVERDUE_DAYS}+ days after their date (U.S. Eastern); "
                 f"Lambda runs and errors cover the last {LOOKBACK_HOURS} hours. A sport out of season has no recent runs."),
    }
