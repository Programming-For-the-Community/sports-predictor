"""
Denied-traffic aggregates the admin assistant answers traffic questions from
(aws-lambdas/shared/assistant-traffic-aggregate). Logs Insights counts the
blocked/denied requests in each log group per hour; the counts land in S3 so
the agent never reads raw log lines, and keep after the logs expire.

S3 layout under the model-artifacts bucket:
  assistant/admin/traffic/hour/<yyyy-mm-ddThh>.json
  assistant/admin/traffic/day/<yyyy-mm-dd>.json   (sum of that day's hours)

Request paths are written by whoever sends the request, so they are reduced
to known route templates or sanitised before they are stored -- the agent
reads them as data.
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

PREFIX = "assistant/admin/traffic"
MAX_PATH = 80
UNPRINTABLE = "[unprintable path]"
QUERY_TIMEOUT_SECONDS = 120

# WAF log records (formatVersion 1): one per request the web ACL saw.
WAF_QUERY = (
    "filter action = 'BLOCK' "
    "| stats count(*) as n by bin(1h) as hour, httpRequest.clientIp as ip, httpRequest.country as country, "
    "terminatingRuleId as rule, httpRequest.httpMethod as method, httpRequest.uri as path "
    "| limit 10000"
)
# API Gateway access log (Terraform/api-gateway-access-logs.tf). Behind
# CloudFront its source IP is an edge location's, so there is no ip/country.
ACCESS_QUERY = (
    "filter status = '401' or status = '403' or status = '429' "
    "| stats count(*) as n by bin(1h) as hour, status as rule, httpMethod as method, resourcePath as path "
    "| limit 10000"
)

_SAFE_PATH = re.compile(r"^[A-Za-z0-9/._~-]*$")
# A sport segment is any short lowercase key; the route after it is what marks an app route.
_SPORT = "[a-z0-9]{2,12}"
_API_ROUTES = [
    (re.compile(rf"^/({_SPORT})/predictions/events/[^/]+/players/[^/]+/?$"), "/{sport}/predictions/events/{event_id}/players/{entity_id}"),
    (re.compile(rf"^/({_SPORT})/predictions/events/[^/]+/?$"), "/{sport}/predictions/events/{event_id}"),
    (re.compile(rf"^/({_SPORT})/events/[^/]+/?$"), "/{sport}/events/{event_id}"),
    (re.compile(rf"^/({_SPORT})/(events|models|season|live-scores|model-performance|performance)/?$"), None),
]
_FRONTEND = re.compile(
    r"^/($|index\.html$|main\.dart\.js$|flutter[\w.]*\.js$|manifest\.json$|favicon\.png$|version\.json$|"
    r"assets/|canvaskit/|icons/|app/|assistant/)"
)


def hour_key(hour: datetime) -> str:
    return f"{PREFIX}/hour/{hour.strftime('%Y-%m-%dT%H')}.json"


def day_key(day: date) -> str:
    return f"{PREFIX}/day/{day.isoformat()}.json"


def classify_path(raw: str | None) -> tuple[str, str]:
    """(path to store, "route" or "probe"). App routes become templates."""
    path = (raw or "").split("?", 1)[0]
    for pattern, template in _API_ROUTES:
        match = pattern.match(path)
        if match:
            return (template.replace("{sport}", match.group(1)) if template else path.rstrip("/")), "route"
    if _FRONTEND.match(path):
        return path if len(path) <= MAX_PATH else path[:MAX_PATH], "route"
    path = path[:MAX_PATH]
    if not path or not _SAFE_PATH.match(path):
        return UNPRINTABLE, "probe"
    return path, "probe"


def run_query(logs, log_group: str, query: str, start: datetime, end: datetime) -> list[dict]:
    query_id = logs.start_query(
        logGroupName=log_group,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()) - 1,
        queryString=query,
    )["queryId"]
    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    while True:
        response = logs.get_query_results(queryId=query_id)
        if response["status"] == "Complete":
            return [{field["field"]: field["value"] for field in row} for row in response["results"]]
        if response["status"] in ("Failed", "Cancelled", "Timeout") or time.monotonic() > deadline:
            raise RuntimeError(f"Logs Insights query on {log_group} ended {response['status']}")
        time.sleep(1)


def _hour_of(value: str) -> str:
    # bin(1h) comes back as "2026-11-06 13:00:00.000".
    return value[:13].replace(" ", "T")


def rows_by_hour(source: str, rows: list[dict]) -> dict[str, list[dict]]:
    hours: dict[str, dict[tuple, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        path, kind = classify_path(row.get("path"))
        key = (
            row.get("ip") or None,
            row.get("country") or None,
            row.get("rule") or "unknown",
            (row.get("method") or "").upper()[:10] or None,
            path,
            kind,
        )
        hours[_hour_of(row["hour"])][key] += int(float(row.get("n", 0)))
    return {
        hour: [
            {"source": source, "ip": k[0], "country": k[1], "rule": k[2], "method": k[3], "path": k[4], "kind": k[5], "n": n}
            for k, n in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
        for hour, counts in hours.items()
    }


def sum_rows(row_lists: list[list[dict]]) -> list[dict]:
    totals: dict[tuple, int] = defaultdict(int)
    for rows in row_lists:
        for r in rows:
            totals[(r["source"], r["ip"], r["country"], r["rule"], r["method"], r["path"], r["kind"])] += r["n"]
    return [
        {"source": k[0], "ip": k[1], "country": k[2], "rule": k[3], "method": k[4], "path": k[5], "kind": k[6], "n": n}
        for k, n in sorted(totals.items(), key=lambda kv: -kv[1])
    ]


def run(sources: list[tuple[str, object, str, str]], s3, start: datetime, end: datetime) -> dict:
    """sources: (name, logs client, log group, query). Rewrites every hour in
    [start, end) -- empty hours included, so a re-run clears stale counts --
    then rebuilds each touched day."""
    start = start.replace(minute=0, second=0, microsecond=0)
    hours: dict[str, list[dict]] = {}
    cursor = start
    while cursor < end:
        hours[cursor.strftime("%Y-%m-%dT%H")] = []
        cursor += timedelta(hours=1)

    for name, logs, group, query in sources:
        for hour, rows in rows_by_hour(name, run_query(logs, group, query, start, end)).items():
            if hour in hours:
                hours[hour].extend(rows)

    for hour, rows in hours.items():
        stamp = datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
        s3.put_json(hour_key(stamp), {"hour": hour, "rows": sum_rows([rows])})

    days = sorted({hour[:10] for hour in hours})
    for day in days:
        keys = sorted(s3.list_keys(f"{PREFIX}/hour/{day}T"))
        rows = sum_rows([s3.get_json(k)["rows"] for k in keys])
        s3.put_json(day_key(date.fromisoformat(day)), {"date": day, "hours": len(keys), "rows": rows})

    return {"hours_written": len(hours), "days_rolled": days, "blocked": sum(r["n"] for rows in hours.values() for r in rows)}
