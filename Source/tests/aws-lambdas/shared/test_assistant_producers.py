"""
assistant-cost-snapshot and assistant-traffic-aggregate handler wiring only --
the work itself is covered by tests/library/assistant/.
"""
import sys
from datetime import timedelta
from unittest.mock import patch

cost_handler = sys.modules["shared_assistant_cost_snapshot"]
traffic_handler = sys.modules["shared_assistant_traffic_aggregate"]


def test_cost_snapshot_passes_its_config_and_the_backfill_flag(monkeypatch):
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("PROJECT_BUDGET_NAME", "sports-predictor-monthly")
    calls = []

    def run(ce, budgets, s3, account_id, budget_name, today, settings, backfill):
        calls.append((account_id, budget_name, backfill))
        return {"days_written": 3}

    with patch.object(cost_handler.cost_snapshot, "run", run):
        assert cost_handler.lambda_handler({}, None) == {"days_written": 3}
        cost_handler.lambda_handler({"backfill": True}, None)

    assert calls == [("123456789012", "sports-predictor-monthly", False), ("123456789012", "sports-predictor-monthly", True)]


def _set_log_groups(monkeypatch):
    monkeypatch.setenv("WAF_CLOUDFRONT_LOG_GROUP", "aws-waf-logs-cf")
    monkeypatch.setenv("WAF_API_GATEWAY_LOG_GROUP", "aws-waf-logs-api")
    monkeypatch.setenv("API_GATEWAY_ACCESS_LOG_GROUP", "/aws/apigateway/access")


def test_traffic_aggregate_rewrites_the_last_whole_hours_from_all_three_sources(monkeypatch):
    _set_log_groups(monkeypatch)
    monkeypatch.setenv("TRAFFIC_LOOKBACK_HOURS", "3")
    windows = []

    def run(sources, s3, start, end):
        windows.append((start, end, [(name, group) for name, _, group, _ in sources]))
        return {"hours_written": 2, "days_rolled": [], "blocked": 0}

    with patch.object(traffic_handler.traffic, "run", run):
        traffic_handler.lambda_handler({}, None)

    start, end, sources = windows[0]
    assert end.minute == 0 and end.second == 0
    assert end - start == timedelta(hours=3)
    assert sources == [("waf_cloudfront", "aws-waf-logs-cf"), ("waf_api", "aws-waf-logs-api"), ("api_gateway", "/aws/apigateway/access")]


def test_traffic_backfill_runs_one_window_per_day(monkeypatch):
    _set_log_groups(monkeypatch)
    windows = []

    def run(sources, s3, start, end):
        windows.append((start, end))
        return {"hours_written": 24, "days_rolled": [start.date().isoformat()], "blocked": 1}

    with patch.object(traffic_handler.traffic, "run", run):
        totals = traffic_handler.lambda_handler({"backfill_days": 3}, None)

    assert len(windows) == 3
    assert all(end - start == timedelta(days=1) for start, end in windows)
    assert windows[0][0] == windows[1][1]
    assert totals["hours_written"] == 72 and totals["blocked"] == 3
