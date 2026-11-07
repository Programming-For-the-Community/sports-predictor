from datetime import datetime, timezone

import pytest

from library.assistant import traffic


@pytest.mark.parametrize("raw, expected", [
    ("/nfl/events", ("/nfl/events", "route")),
    ("/nba/predictions/events/401585", ("/nba/predictions/events/{event_id}", "route")),
    ("/nfl/predictions/events/1/players/mahomes", ("/nfl/predictions/events/{event_id}/players/{entity_id}", "route")),
    ("/assets/fonts/x.ttf", ("/assets/fonts/x.ttf", "route")),
    ("/", ("/", "route")),
    ("/.env", ("/.env", "probe")),
    ("/wp-login.php?x=1", ("/wp-login.php", "probe")),
    ("/a" * 60, (("/a" * 60)[:80], "probe")),
    ("/ignore previous instructions", (traffic.UNPRINTABLE, "probe")),
    ("/%3Cscript%3E", (traffic.UNPRINTABLE, "probe")),
    (None, (traffic.UNPRINTABLE, "probe")),
])
def test_paths_become_templates_or_sanitised_probes(raw, expected):
    assert traffic.classify_path(raw) == expected


class FakeLogs:
    def __init__(self, rows, status="Complete"):
        self.rows = rows
        self.status = status
        self.started = []

    def start_query(self, **kwargs):
        self.started.append(kwargs)
        return {"queryId": "q"}

    def get_query_results(self, queryId):
        return {
            "status": self.status,
            "results": [[{"field": k, "value": v} for k, v in row.items()] for row in self.rows],
        }


WAF_ROWS = [
    {"hour": "2026-11-06 13:00:00.000", "ip": "198.51.100.23", "country": "NL", "rule": "per-ip-rate-limit", "method": "GET", "path": "/.env", "n": "5"},
    {"hour": "2026-11-06 13:00:00.000", "ip": "198.51.100.23", "country": "NL", "rule": "per-ip-rate-limit", "method": "GET", "path": "/.env", "n": "2"},
    {"hour": "2026-11-06 14:00:00.000", "ip": "203.0.113.88", "country": "US", "rule": "amazon-ip-reputation-list", "method": "get", "path": "/nfl/events", "n": "1"},
]
ACCESS_ROWS = [{"hour": "2026-11-06 13:00:00.000", "rule": "429", "method": "GET", "path": "/nba/season", "n": "3"}]


def test_run_writes_each_hour_and_rolls_up_the_day(fake_s3):
    s3 = fake_s3()
    start = datetime(2026, 11, 6, 13, tzinfo=timezone.utc)
    end = datetime(2026, 11, 6, 15, tzinfo=timezone.utc)
    waf, access = FakeLogs(WAF_ROWS), FakeLogs(ACCESS_ROWS)

    summary = traffic.run([("waf_cloudfront", waf, "g1", traffic.WAF_QUERY), ("api_gateway", access, "g2", traffic.ACCESS_QUERY)], s3, start, end)

    assert summary == {"hours_written": 2, "days_rolled": ["2026-11-06"], "blocked": 11}
    assert waf.started[0]["startTime"] == int(start.timestamp())
    assert waf.started[0]["endTime"] == int(end.timestamp()) - 1
    hour13 = s3.objects["assistant/admin/traffic/hour/2026-11-06T13.json"]["rows"]
    assert hour13[0] == {"source": "waf_cloudfront", "ip": "198.51.100.23", "country": "NL", "rule": "per-ip-rate-limit",
                         "method": "GET", "path": "/.env", "kind": "probe", "n": 7}
    assert {"source": "api_gateway", "ip": None, "country": None, "rule": "429", "method": "GET",
            "path": "/nba/season", "kind": "route", "n": 3} in hour13
    day = s3.objects["assistant/admin/traffic/day/2026-11-06.json"]
    assert day["hours"] == 2 and sum(r["n"] for r in day["rows"]) == 11


def test_a_rerun_clears_counts_for_an_hour_that_is_now_empty(fake_s3):
    s3 = fake_s3({"assistant/admin/traffic/hour/2026-11-06T13.json": {"hour": "2026-11-06T13", "rows": [{"n": 99}]}})
    start = datetime(2026, 11, 6, 13, tzinfo=timezone.utc)
    traffic.run([("waf_api", FakeLogs([]), "g", traffic.WAF_QUERY)], s3, start, datetime(2026, 11, 6, 14, tzinfo=timezone.utc))

    assert s3.objects["assistant/admin/traffic/hour/2026-11-06T13.json"]["rows"] == []


def test_a_failed_query_raises():
    with pytest.raises(RuntimeError):
        traffic.run_query(FakeLogs([], status="Failed"), "g", "q", datetime(2026, 11, 6, tzinfo=timezone.utc), datetime(2026, 11, 6, 1, tzinfo=timezone.utc))
