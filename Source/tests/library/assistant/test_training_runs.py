"""Training runs: each orchestrator run's wall time and each model's task time."""
from datetime import date, datetime, timezone

from library.assistant import admin_tools, training_runs
from library.assistant.settings import ToolSettings


def _task(container, start_ms, stop_ms, target=None, exit_code=0):
    environment = [{"Name": "TARGET_STAT", "Value": target}] if target else []
    return {"TaskArn": "t", "Containers": [{"Name": container, "ExitCode": exit_code}], "StartedAt": start_ms, "StoppedAt": stop_ms,
            "Overrides": {"ContainerOverrides": [{"Name": container, "Environment": environment}]}}


def _at(day, hour):
    return datetime(2026, 11, day, hour, tzinfo=timezone.utc)


class FakeStepFunctions:
    def __init__(self, runs):
        self.runs = runs  # newest first: (arn, start, stop, status, tasks)

    def get_paginator(self, name):
        runs = self.runs

        class Pages:
            def paginate(self, stateMachineArn):
                yield {"executions": [{"executionArn": a, "startDate": s, "stopDate": e, "status": st} for a, s, e, st, _ in runs]}
        return Pages()

    def describe_execution(self, executionArn):
        import json
        tasks = next(t for a, *_, t in self.runs if a == executionArn)
        return {"output": json.dumps([[task] for task in tasks])}


HOUR_MS = 3_600_000
RUNS = FakeStepFunctions([
    ("run-3", _at(8, 12), _at(8, 15), "SUCCEEDED", [
        _task("nfl-train-win-probability-model", 0, HOUR_MS // 2),
        _task("nba-train-player-prop-model", 0, 2 * HOUR_MS, "points"),
        _task("nba-train-player-prop-model", 0, HOUR_MS, "assists", exit_code=1),
    ]),
    ("run-2", _at(1, 12), _at(1, 13), "SUCCEEDED", [_task("nba-train-player-prop-model", 0, HOUR_MS, "points")]),
    ("run-1", datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, 1, tzinfo=timezone.utc), "FAILED", []),
])


def test_runs_in_range_with_wall_time_task_times_and_failures():
    result = training_runs.report(RUNS, "arn", date(2026, 11, 1), date(2026, 11, 9))

    assert [r["started_utc"] for r in result["runs"]] == ["2026-11-08 12:00", "2026-11-01 12:00"]
    assert result["runs"][0]["wall_minutes"] == 180.0
    assert result["runs"][0]["failed_tasks"] == 1
    assert result["runs"][0]["slowest"][0] == "nba player-prop (points): 120.0 min"
    assert result["summary"] == {"runs": 2, "succeeded": 2, "avg_wall_minutes": 120.0}
    points = next(m for m in result["slowest_models"] if m["model"] == "player-prop (points)")
    assert points == {"sport": "nba", "model": "player-prop (points)", "runs": 2, "avg_minutes": 90.0, "max_minutes": 120.0}


def test_a_sport_narrows_the_per_model_times():
    result = training_runs.report(RUNS, "arn", date(2026, 11, 1), date(2026, 11, 9), "nfl")
    assert [m["sport"] for m in result["slowest_models"]] == ["nfl"]


def test_the_admin_tool_reads_the_range_it_was_asked_for(fake_s3):
    settings = ToolSettings(max_rows=25, max_result_chars=12_000, max_trend_points=400, hourly_trend_days=7, repeat_min_blocks=100, repeat_min_days=3)
    ctx = admin_tools.AdminContext(s3=fake_s3(), settings=settings, stepfunctions=RUNS, training_state_machine_arn="arn", sports=lambda: ["nba", "nfl"])
    result = admin_tools.run("get_training_runs", {"start_date": "2026-11-05", "end_date": "2026-11-09"}, ctx)
    assert result["summary"]["runs"] == 1
