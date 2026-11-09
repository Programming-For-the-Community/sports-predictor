"""Assistant usage counting and the admin agent's pipeline-health and usage tools."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from _turn_fakes import PUBLIC, SETTINGS, SPORTS, FakeGuardrails, FakeModel, _call, _response, _text, make_clients, run

from library.assistant import admin_tools, pipeline_health, usage
from library.assistant.prompts import build_profile
from library.assistant.settings import AgentSettings, ToolSettings

TOOL_SETTINGS = ToolSettings(max_rows=25, max_result_chars=12_000, max_trend_points=400, hourly_trend_days=7, repeat_min_blocks=100, repeat_min_days=3)
DAY = date(2026, 11, 6)


def _used(response, input_tokens, output_tokens):
    response.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


def _usage_row(table, agent="public", day=DAY):
    return table.items.get((f"usage#{agent}", day.isoformat()))


# --- counting in the turn ---------------------------------------------------------------------

def test_an_answer_adds_its_tokens_lookups_and_user_to_the_day(fake_table):
    table = fake_table()
    model = FakeModel([
        _used(_response(_call("list_events", {"sport": "nfl", "when": "recent"}), stop="tool_use"), 1000, 40),
        _used(_response(_text("7 of 10 right.")), 1200, 25),
    ])
    clients, _ = make_clients(model, table)

    run(PUBLIC, clients)
    run(PUBLIC, make_clients(FakeModel([_used(_response(_text("Hi.")), 300, 5)]), table)[0])

    row = _usage_row(table)
    assert row["questions"] == 2
    assert row["answered"] == 2
    assert row["input_tokens"] == 2500
    assert row["output_tokens"] == 70
    assert row["tool_calls"] == 1
    assert row["users"] == {"user-1"}


def test_blocked_and_quota_outcomes_are_counted_without_a_model_call(fake_table):
    table = fake_table()
    limited = build_profile("public", AgentSettings(**{**SETTINGS.__dict__, "daily_limit": 1}), SPORTS)

    run(limited, make_clients(FakeModel([]), table, guardrails=FakeGuardrails(block_input=True))[0], "Bet the Lions?")
    run(limited, make_clients(FakeModel([]), table)[0])

    row = _usage_row(table)
    assert (row["questions"], row["blocked"], row["quota"]) == (2, 1, 1)
    assert row["input_tokens"] == 0


def test_a_failed_usage_write_never_fails_the_answer(fake_table):
    class BrokenTable(fake_table):
        def update_item(self, **kwargs):
            if kwargs["Key"]["pk"].startswith("usage#"):
                raise RuntimeError("throttled")
            return super().update_item(**kwargs)

    clients, _ = make_clients(FakeModel([_response(_text("Fine."))]), BrokenTable())
    assert run(PUBLIC, clients)[-1]["text"] == "Fine."


def test_status_checks_are_not_questions(fake_table):
    table = fake_table()
    clients, _ = make_clients(FakeModel([]), table)
    run(PUBLIC, clients, action="status")
    assert _usage_row(table) is None


# --- get_assistant_usage ----------------------------------------------------------------------

def test_the_usage_report_totals_each_agent_over_the_range(fake_table):
    table = fake_table()
    usage.record(table, "public", DAY, "a", "answered", 100, 10, 2)
    usage.record(table, "public", DAY, "b", "blocked")
    usage.record(table, "public", DAY + timedelta(days=1), "a", "answered", 50, 5, 1)
    usage.record(table, "admin", DAY, "admin-1", "failed", 20, 0, 0)
    usage.record(table, "public", DAY - timedelta(days=30), "a", "answered", 999, 9, 9)  # outside the range

    result = usage.report(table, {}, DAY, DAY + timedelta(days=1), 25)

    public = result["agents"]["public"]
    assert public["totals"]["questions"] == 3
    assert public["totals"]["blocked"] == 1
    assert public["totals"]["input_tokens"] == 150
    assert public["totals"]["user_days"] == 3
    assert public["totals"]["tool_calls_per_question"] == 1.0
    assert [d["date"] for d in public["days"]] == ["2026-11-06", "2026-11-07"]
    assert result["agents"]["admin"]["totals"]["failed"] == 1
    assert list(usage.report(table, {"agent": "admin"}, DAY, DAY, 25)["agents"]) == ["admin"]


def test_an_unknown_outcome_is_not_recorded(fake_table):
    table = fake_table()
    usage.record(table, "public", DAY, "a", "status")
    assert table.items == {}


# --- get_pipeline_health ----------------------------------------------------------------------

class FakeEvents:
    """The events table's sport-status-index: rows by sport#status, filtered
    on event_date the way the tool's conditions ask."""

    def __init__(self, rows):
        self.rows = rows

    def query(self, IndexName, KeyConditionExpression, ScanIndexForward=True, Limit=None, Select=None, ExclusiveStartKey=None):
        assert IndexName == "sport-status-index"
        conditions = KeyConditionExpression._values if KeyConditionExpression.expression_operator == "AND" else (KeyConditionExpression,)
        rows = [r for r in self.rows if f"{r['sport']}#{r['status']}" == conditions[0]._values[1]]
        for condition in conditions[1:]:
            op, value = condition.expression_operator, condition._values[1]
            rows = [r for r in rows if (r["event_date"] >= value if op == ">=" else r["event_date"] <= value)]
        rows.sort(key=lambda r: r["event_date"], reverse=not ScanIndexForward)
        if Select == "COUNT":
            return {"Count": len(rows)}
        return {"Items": rows[:Limit] if Limit else rows}


class FakeCloudWatch:
    def __init__(self, ran):
        self.ran = ran  # function name -> (runs, errors, last hour)
        self.asked = []

    def get_metric_data(self, MetricDataQueries, StartTime, EndTime):
        self.asked += MetricDataQueries
        results = []
        for query in MetricDataQueries:
            function = query["MetricStat"]["Metric"]["Dimensions"][0]["Value"]
            runs, errors, last = self.ran.get(function, (0, 0, None))
            value = runs if query["MetricStat"]["Metric"]["MetricName"] == "Invocations" else errors
            results.append({"Id": query["Id"], "Timestamps": [last] if last and value else [], "Values": [value] if value else []})
        return {"MetricDataResults": results}


def _event(event_id, status, event_date):
    return {"sport": "ncaambb", "event_id": event_id, "status": status, "event_date": event_date, "kickoff_time": f"{event_date}T23:00Z"}


def test_pipeline_health_reports_freshness_overdue_games_and_lambda_runs():
    events = FakeEvents([
        _event("1", "completed", "2026-11-06"), _event("2", "completed", "2026-11-07"),
        _event("3", "scheduled", "2026-11-05"), _event("4", "scheduled", "2026-11-07"), _event("5", "scheduled", "2026-11-09"),
    ])
    now = datetime(2026, 11, 8, 15, 30, tzinfo=timezone.utc)
    cloudwatch = FakeCloudWatch({"sports-predictor-ncaambb-ingest": (2, 1, datetime(2026, 11, 8, 10, tzinfo=timezone.utc))})

    result = pipeline_health.pipeline_health(events, cloudwatch, "sports-predictor", ["ncaambb"], date(2026, 11, 8), now)

    sport = result["sports"][0]
    assert sport["last_finished_game"]["event_id"] == "2"
    assert sport["next_scheduled_game"]["event_id"] == "5"
    assert sport["overdue_scheduled"] == 1  # "4" is only a day past
    assert sport["overdue_examples"] == [{"event_id": "3", "event_date": "2026-11-05", "kickoff_time": "2026-11-05T23:00Z"}]
    ingest = next(row for row in sport["lambdas"] if row["stage"] == "ingest")
    assert ingest == {"stage": "ingest", "runs": 2, "errors": 1, "last_run_hour_utc": "2026-11-08 10:00"}
    assert next(row for row in sport["lambdas"] if row["stage"] == "normalize")["last_run_hour_utc"] is None
    assert len(cloudwatch.asked) == 2 * len(pipeline_health.STAGES)


def test_admin_tools_run_pipeline_health_for_one_sport_or_all(fake_s3, monkeypatch):
    seen = []
    ctx = admin_tools.AdminContext(s3=fake_s3(), settings=TOOL_SETTINGS, events_table=FakeEvents([]), cloudwatch=FakeCloudWatch({}),
                                   project="sports-predictor", sports=lambda: ["nba", "nfl"], today=lambda: date(2026, 11, 8))
    original = pipeline_health.games
    monkeypatch.setattr(pipeline_health, "games", lambda table, sport, today: seen.append(sport) or original(table, sport, today))

    assert [s["sport"] for s in admin_tools.run("get_pipeline_health", {}, ctx)["sports"]] == ["nba", "nfl"]
    assert [s["sport"] for s in admin_tools.run("get_pipeline_health", {"sport": "nfl"}, ctx)["sports"]] == ["nfl"]
    with pytest.raises(ValueError):
        admin_tools.run("get_pipeline_health", {"sport": "cricket"}, ctx)
    assert sorted(seen) == ["nba", "nfl", "nfl"]
