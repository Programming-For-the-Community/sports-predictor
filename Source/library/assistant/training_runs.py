"""
The admin agent's training-run report: each run of the training
orchestrator (Terraform: sports-predictor-training-orchestrator) in a date
range -- when it started, how long it took end to end, and how long each
model's ECS training task took, read from the run's own output (the ECS
task results it collected). A task is labelled by its container
("<sport>-train-<kind>-model") and the stat or score it trained.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

TARGET_VARIABLES = ("TARGET_STAT", "SCORE_TARGET", "TARGET")
SLOWEST_PER_RUN = 3
MODEL_ROWS = 15


def _minutes(start, stop) -> float | None:
    if start is None or stop is None:
        return None
    begin = start.timestamp() if isinstance(start, datetime) else start / 1000
    end = stop.timestamp() if isinstance(stop, datetime) else stop / 1000
    return round((end - begin) / 60, 1)


def _ecs_tasks(value):
    if isinstance(value, dict):
        if "TaskArn" in value and "Containers" in value:
            yield value
            return
        for item in value.values():
            yield from _ecs_tasks(item)
    elif isinstance(value, list):
        for item in value:
            yield from _ecs_tasks(item)


def _label(task: dict) -> tuple[str, str]:
    """(sport, model) from the container name and its target override."""
    override = ((task.get("Overrides") or {}).get("ContainerOverrides") or [{}])[0]
    name = override.get("Name") or (task.get("Containers") or [{}])[0].get("Name") or "?"
    sport, _, kind = name.partition("-train-")
    kind = kind.removesuffix("-model")
    target = next((e["Value"] for e in override.get("Environment") or [] if e.get("Name") in TARGET_VARIABLES), None)
    return sport, f"{kind} ({target})" if target else kind


def task_rows(output) -> list[dict]:
    rows = []
    for task in _ecs_tasks(output):
        sport, model = _label(task)
        exit_code = (task.get("Containers") or [{}])[0].get("ExitCode")
        rows.append({"sport": sport, "model": model, "minutes": _minutes(task.get("StartedAt"), task.get("StoppedAt")), "ok": exit_code == 0})
    return rows


def _executions(sfn, arn: str, begin: datetime, finish: datetime) -> list[dict]:
    """The runs that started in [begin, finish), newest first."""
    found = []
    for page in sfn.get_paginator("list_executions").paginate(stateMachineArn=arn):
        for execution in page["executions"]:
            started = execution["startDate"]
            if started < begin:
                return found
            if started < finish:
                found.append(execution)
    return found


def _run(sfn, execution: dict) -> tuple[dict, list[dict]]:
    detail = sfn.describe_execution(executionArn=execution["executionArn"])
    output = detail.get("output")
    tasks = task_rows(json.loads(output)) if output else []
    timed = sorted((t for t in tasks if t["minutes"] is not None), key=lambda t: -t["minutes"])
    run = {"started_utc": execution["startDate"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M"), "status": execution["status"],
           "wall_minutes": _minutes(execution["startDate"], execution.get("stopDate")), "tasks": len(tasks),
           "failed_tasks": sum(1 for t in tasks if not t["ok"]),
           "slowest": [f"{t['sport']} {t['model']}: {t['minutes']} min" for t in timed[:SLOWEST_PER_RUN]]}
    return run, tasks


def report(sfn, arn: str, start: date, end: date, sport: str | None = None) -> dict:
    begin = datetime.combine(start, time.min, tzinfo=timezone.utc)
    finish = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    runs, per_model = [], defaultdict(list)
    for execution in _executions(sfn, arn, begin, finish):
        run, tasks = _run(sfn, execution)
        runs.append(run)
        for task in tasks:
            if task["minutes"] is not None and (not sport or task["sport"] == sport):
                per_model[(task["sport"], task["model"])].append(task["minutes"])
    finished = [r["wall_minutes"] for r in runs if r["status"] == "SUCCEEDED" and r["wall_minutes"] is not None]
    models = sorted(({"sport": s, "model": m, "runs": len(v), "avg_minutes": round(sum(v) / len(v), 1), "max_minutes": max(v)}
                     for (s, m), v in per_model.items()), key=lambda row: -row["avg_minutes"])
    return {
        "start_date": start.isoformat(), "end_date": end.isoformat(), "runs": runs,
        "summary": {"runs": len(runs), "succeeded": len(finished),
                    "avg_wall_minutes_of_succeeded_runs": round(sum(finished) / len(finished), 1) if finished else None},
        "slowest_models": models[:MODEL_ROWS],
        "note": "wall_minutes is a whole run, start to finish, with sports training side by side; a model's minutes are its own ECS task.",
    }
