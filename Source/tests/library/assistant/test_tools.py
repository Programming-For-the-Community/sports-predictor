import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from library.assistant import admin_tools, cost_snapshot, date_ranges, public_tools, tool_specs, traffic
from library.assistant.settings import ToolSettings

SPORTS = ["f1", "nba", "ncaafb", "ncaambb", "nfl", "nhl", "pga"]
SETTINGS = ToolSettings(max_rows=25, max_result_chars=12_000, max_trend_points=400, hourly_trend_days=7, repeat_min_blocks=100, repeat_min_days=3)


# --- tool_specs -----------------------------------------------------------------

def test_the_public_agent_tools_never_include_an_admin_tool():
    assert not tool_specs.PUBLIC_TOOL_NAMES & tool_specs.ADMIN_TOOL_NAMES
    public = {t["name"] for t in tool_specs.anthropic_tools(tool_specs.PUBLIC_TOOL_NAMES | tool_specs.FAVORITE_TOOL_NAMES, SPORTS)}
    assert public == {"list_events", "get_event_prediction", "get_model_performance", "get_season_projection", "save_favorite", "clear_favorites"}


def test_sports_come_from_the_registry_list():
    sport = tool_specs.anthropic_tools({"list_events"}, ["nfl", "wnba"])[0]["input_schema"]["properties"]["sport"]
    assert sport["enum"] == ["nfl", "wnba"]
    assert tool_specs.validate("list_events", {"sport": "wnba", "when": "recent"}, ["wnba"])["sport"] == "wnba"


@pytest.mark.parametrize("name, args, message", [
    ("list_events", {"sport": "nfl"}, "Missing"),
    ("list_events", {"sport": "cricket", "when": "upcoming"}, "one of"),
    ("list_events", {"sport": "nfl", "when": "upcoming", "limit": 500}, "Unknown argument"),
    ("get_denied_trend", {"start_date": "2026-13-01"}, "real date"),
    ("get_denied_trend", {"start_date": "yesterday"}, "YYYY-MM-DD"),
    ("get_repeat_offenders", {"min_blocks": 0}, "positive"),
    ("get_repeat_offenders", {"min_blocks": True}, "positive"),
    ("no_such_tool", {}, "Unknown tool"),
])
def test_invalid_arguments_are_refused(name, args, message):
    with pytest.raises(tool_specs.ToolInputError, match=message):
        tool_specs.validate(name, args, SPORTS)


# --- public_tools -----------------------------------------------------------------

CACHED_PREDICTION = {
    "event_key": "SPORT#NFL#EVENT#1",
    "predictions": {"win_probability": {"home_win_probability": 0.71234567, "model_version": 10}, "margin": {"value": -6.53, "model_version": 11}},
    "leaders": {"home": {"passing": {"entity_id": "391", "passing_yards": 221.634, "name": "A Passer"}}},
    "generated_at": "2026-11-04T23:53:14+00:00",
}


class FakeReader:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, resource, path_params, query_params):
        self.calls.append((resource, path_params, query_params))
        return self.responses[resource]


def test_event_prediction_strips_ids_and_bookkeeping_and_rounds():
    read = FakeReader({"/nfl/predictions/events/{event_id}": (200, {"result": CACHED_PREDICTION})})
    result = public_tools.run("get_event_prediction", {"sport": "nfl", "event_id": "1"}, read, SPORTS, SETTINGS)

    assert read.calls == [("/nfl/predictions/events/{event_id}", {"event_id": "1"}, {})]
    text = json.dumps(result)
    assert "entity_id" not in text and "model_version" not in text and "event_key" not in text
    assert result["predictions"]["win_probability"]["home_win_probability"] == 0.712
    assert result["leaders"]["home"]["passing"]["name"] == "A Passer"


def test_a_cache_miss_tells_the_model_to_try_again():
    read = FakeReader({"/nba/predictions/events/{event_id}": (202, {"retry_after_seconds": 30})})
    assert public_tools.run("get_event_prediction", {"sport": "nba", "event_id": "9"}, read, SPORTS, SETTINGS)["pending"] is True


def test_list_events_maps_when_to_the_status_query_and_caps_rows():
    events = [{"event_id": str(i), "status": "scheduled", "participants": []} for i in range(40)]
    read = FakeReader({"/nhl/events": (200, {"sport": "nhl", "events": events})})
    result = public_tools.run("list_events", {"sport": "nhl", "when": "upcoming"}, read, SPORTS, SETTINGS)

    assert read.calls[0][2] == {"status": "scheduled"}
    assert result["count"] == 40 and len(result["events"]) <= SETTINGS.max_rows


def test_model_performance_can_be_narrowed_to_one_model_and_keeps_the_trend():
    body = {"season": 2026, "models": [
        {"model_name": "win-probability", "version": 10, "season": {"value": 0.64, "n": 120}, "history": [{"label": f"Wk {i}", "value": 0.6} for i in range(40)], "periods": []},
        {"model_name": "margin", "version": 11, "season": {"value": 9.1, "n": 120}},
    ]}
    read = FakeReader({"/nfl/model-performance": (200, body)})
    result = public_tools.run("get_model_performance", {"sport": "nfl", "model": "win-probability"}, read, SPORTS, SETTINGS)

    assert [m["model_name"] for m in result["models"]] == ["win-probability"]
    assert len(result["models"][0]["history"]) == public_tools.MAX_HISTORY
    assert "periods" not in result["models"][0]

    missing = public_tools.run("get_model_performance", {"sport": "nfl", "model": "nope"}, read, SPORTS, SETTINGS)
    assert missing["models"] == ["margin", "win-probability"]


def test_an_oversized_answer_is_shrunk_to_fit():
    huge = {"standings": [{"name": "x" * 200, "values": list(range(100))} for _ in range(200)]}
    result = public_tools.run("get_season_projection", {"sport": "nba"}, FakeReader({"/nba/season": (200, huge)}), SPORTS, SETTINGS)
    assert len(json.dumps(result)) <= SETTINGS.max_result_chars


# --- admin_tools ------------------------------------------------------------------

TODAY = date(2026, 11, 6)


def _row(source, ip, country, rule, path, kind, n):
    return {"source": source, "ip": ip, "country": country, "rule": rule, "method": "GET", "path": path, "kind": kind, "n": n}


@pytest.fixture
def traffic_s3(fake_s3):
    s3 = fake_s3()
    for offset, rows in enumerate([
        [_row("waf_cloudfront", "198.51.100.23", "NL", "per-ip-rate-limit", "/.env", "probe", 60),
         _row("waf_api", "203.0.113.88", "US", "Default_Action", "/nfl/events", "route", 5)],
        [_row("waf_cloudfront", "198.51.100.23", "NL", "per-ip-rate-limit", "/.env", "probe", 50),
         _row("api_gateway", None, None, "429", "/nba/season", "route", 7)],
        [_row("waf_cloudfront", "192.0.2.140", "SG", "amazon-ip-reputation-list", "/wp-login.php", "probe", 3)],
    ]):
        day = TODAY - timedelta(days=offset)
        s3.put_json(traffic.day_key(day), {"date": day.isoformat(), "rows": rows})
    return s3


@pytest.fixture(autouse=True)
def _fixed_today():
    with patch.object(date_ranges, "today", return_value=TODAY):
        yield


def test_repeat_offenders_apply_the_thresholds(traffic_s3):
    result = admin_tools.run("get_repeat_offenders", {"start_date": "2026-11-01", "min_blocks": 100}, admin_tools.AdminContext(s3=traffic_s3, settings=SETTINGS))

    assert result["serious_count"] == 1
    assert result["serious"][0]["ip"] == "198.51.100.23"
    assert result["serious"][0]["blocked"] == 110 and result["serious"][0]["days_active"] == 2
    assert result["data_from"] == "2026-11-04"
    assert result["start_date"] == "2026-11-04"


def test_locations_sum_by_country_and_label_access_log_denials_unknown(traffic_s3):
    result = admin_tools.run("get_denied_locations", {"start_date": "2026-11-04"}, admin_tools.AdminContext(s3=traffic_s3, settings=SETTINGS))
    by_country = {c["country"]: c["blocked"] for c in result["by_country"]}

    assert by_country == {"NL": 110, "unknown": 7, "US": 5, "SG": 3}
    assert result["total_blocked"] == 125


def test_endpoints_and_source_filter(traffic_s3):
    result = admin_tools.run("get_denied_endpoints", {"start_date": "2026-11-04", "source": "waf_cloudfront"}, admin_tools.AdminContext(s3=traffic_s3, settings=SETTINGS))

    assert [e["path"] for e in result["endpoints"]] == ["/.env", "/wp-login.php"]
    assert result["endpoints"][0]["kind"] == "probe"


def test_long_trends_are_daily(traffic_s3):
    result = admin_tools.run("get_denied_trend", {"start_date": "2026-10-01"}, admin_tools.AdminContext(s3=traffic_s3, settings=SETTINGS))
    assert result["granularity"] == "day"
    assert [p["at"] for p in result["points"]] == ["2026-11-04", "2026-11-05", "2026-11-06"]


def test_a_range_ending_before_it_starts_is_refused(traffic_s3):
    with pytest.raises(ValueError):
        admin_tools.run("get_denied_trend", {"start_date": "2026-11-06", "end_date": "2026-11-01"}, admin_tools.AdminContext(s3=traffic_s3, settings=SETTINGS))


def test_cost_summary_groups_compares_and_reports_the_budget(fake_s3):
    s3 = fake_s3({cost_snapshot.meta_key(): {"data_from": "2026-10-01", "budget_limit": 120.0, "month_forecast": 118.0, "excluded": ["tax"]}})
    for day in range(1, 7):
        s3.put_json(cost_snapshot.daily_key(date(2026, 11, day)), {"total": 2.0, "by_service": {"AWS Lambda": 1.5, "Amazon S3": 0.5}})
    for day in range(26, 32):
        s3.put_json(cost_snapshot.daily_key(date(2026, 10, day)), {"total": 1.0, "by_service": {"AWS Lambda": 1.0}})

    result = admin_tools.run("get_cost_summary", {}, admin_tools.AdminContext(s3=s3, settings=SETTINGS))

    assert result["start_date"] == "2026-11-01" and result["total"] == 12.0
    assert result["groups"][0] == {"name": "AWS Lambda", "usd": 9.0}
    assert result["previous_period_total"] == 6.0 and result["change_pct"] == 100.0
    assert result["budget_used_pct"] == 10.0 and result["month_forecast"] == 118.0
    assert result["excluded"] == ["tax"]


class FakeCloudWatch:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def get_metric_data(self, MetricDataQueries, StartTime, EndTime):
        self.calls.append(MetricDataQueries)
        return {"MetricDataResults": [{"Id": q["Id"], "Values": [self.values.get(q["Id"], 0.0)]} for q in MetricDataQueries]}


class FakeLambda:
    def get_paginator(self, name):
        functions = [{"FunctionName": "sports-predictor-nfl-ingest", "Timeout": 60},
                     {"FunctionName": "sports-predictor-nba-predict", "Timeout": 30},
                     {"FunctionName": "someone-else", "Timeout": 3}]

        class Paginator:
            def paginate(self):
                return [{"Functions": functions}]
        return Paginator()


def test_lambda_health_lists_only_project_functions_with_problems(fake_s3):
    cloudwatch = FakeCloudWatch({"m0_invocations": 100, "m0_errors": 5, "m0_duration": 1000, "m1_invocations": 50, "m1_duration": 29_500})
    result = admin_tools.run("get_lambda_health", {}, admin_tools.AdminContext(s3=fake_s3(), settings=SETTINGS, cloudwatch=cloudwatch, lambda_client=FakeLambda(), project="sports-predictor"))

    assert result["functions_checked"] == 2
    assert [f["function"] for f in result["functions"]] == ["nfl-ingest", "nba-predict"]
    assert result["functions"][0]["error_pct"] == 5.0
    assert result["functions"][1]["hit_timeout"] is True


def test_api_traffic_reads_api_metrics_and_denied_routes(traffic_s3):
    cloudwatch = FakeCloudWatch({"requests": 1000, "e4xx": 40, "e5xx": 2, "p95": 812.4})
    result = admin_tools.run("get_api_traffic", {"start_date": "2026-11-04"}, admin_tools.AdminContext(s3=traffic_s3, settings=SETTINGS, cloudwatch=cloudwatch, api_name="sports-predictor-api"))

    assert result["requests"] == 1000 and result["latency_p95_ms"] == 812.4
    assert result["denied_by_route"] == [{"status": "429", "method": "GET", "route": "/nba/season", "count": 7}]
    dims = cloudwatch.calls[0][0]["MetricStat"]["Metric"]["Dimensions"]
    assert dims == [{"Name": "ApiName", "Value": "sports-predictor-api"}]
