"""
CloudWatch custom-widget Lambda for the viewer-analytics dashboard's two
geo panels (Terraform/cloudwatch-dashboard-viewer-analytics.tf). CloudWatch
dashboards have no native map widget type -- the only mechanism for an
arbitrary visual is a "custom widget": a Lambda CloudWatch invokes on
render, returning an HTML string shown inside a sandboxed iframe.

NOT a geographically accurate choropleth. No verified state/country
boundary-path data source was available to build against (no geo library
installed, no reliable way to fetch/verify exact literal coordinate data
in this environment) -- rather than embed possibly-wrong boundary
geometry, this renders a schematic TILE-GRID cartogram instead (same
style NPR/538 election dashboards use): each US state is a fixed-position
labeled tile (US_STATE_GRID below, a deliberately approximate layout, not
a projection), shaded by real request count; blocked/world traffic is
grouped into world regions the same way. Approximate placement is a
disclosed, low-risk cosmetic choice here -- unlike fabricated boundary
coordinates, a tile one cell off never misrepresents the underlying data.

Two render modes, chosen by the widget's own `mode` custom-widget
parameter (set per dashboard-widget instance in Terraform):
  "accepted" -- counts accepted requests by US state, from every sport's
    predict-read log group's own viewer_analytics line (library.serving.
    viewer_analytics's `region` field -- a 2-letter code, matches
    US_STATE_GRID's keys directly, no name-to-abbreviation lookup needed).
  "blocked"  -- counts CloudFront-blocked (403) requests by country, from
    the CloudFront edge-access log group (cloudfront-standard-logging.tf),
    grouped into world regions via COUNTRY_REGION.

Runs Logs Insights synchronously (StartQuery + poll GetQueryResults) on
every render -- the log volume here is small (this is a single-user
project), and CloudWatch custom widgets already have their own multi-
second render budget, so a short synchronous poll is simpler than a
separate precompute-to-S3 schedule.
"""
import os
import time

import boto3

# Deliberately approximate (row, col) grid, not a projection -- see this
# module's own docstring. Sized to keep DC/RI/CT legible next to their
# real neighbors without every row needing the same column count.
US_STATE_GRID = {
    "ME": (0, 11),
    "VT": (1, 9), "NH": (1, 10),
    "WA": (2, 0), "ID": (2, 1), "MT": (2, 2), "ND": (2, 3), "MN": (2, 4), "WI": (2, 7), "MI": (2, 8), "NY": (2, 10), "MA": (2, 11),
    "OR": (3, 0), "NV": (3, 1), "WY": (3, 2), "SD": (3, 3), "IA": (3, 5), "IL": (3, 6), "IN": (3, 7), "OH": (3, 8), "PA": (3, 9), "NJ": (3, 10), "CT": (3, 11), "RI": (3, 12),
    "CA": (4, 0), "UT": (4, 1), "CO": (4, 2), "NE": (4, 3), "MO": (4, 5), "KY": (4, 6), "WV": (4, 7), "VA": (4, 8), "MD": (4, 9), "DE": (4, 10),
    "AZ": (5, 1), "NM": (5, 2), "KS": (5, 3), "AR": (5, 4), "TN": (5, 6), "NC": (5, 8), "SC": (5, 9), "DC": (5, 10),
    "OK": (6, 3), "LA": (6, 4), "MS": (6, 5), "AL": (6, 6), "GA": (6, 8),
    "AK": (7, 0), "TX": (7, 3), "FL": (7, 10),
    "HI": (8, 0),
}

# Not exhaustive -- covers the country codes this project's own real
# traffic/blocked-attempt data has ever plausibly needed (US-whitelisted
# app, so "blocked" is almost entirely non-US); anything unmapped falls
# into "Other" rather than being dropped, so an unrecognized code is
# still visible on the dashboard, just ungrouped.
COUNTRY_REGION = {
    **{c: "Americas" for c in ["US", "CA", "MX", "BR", "AR", "CO", "CL", "PE", "VE", "CU", "DO", "GT", "EC", "BO", "PY", "UY", "CR", "PA", "JM", "HT", "TT"]},
    **{c: "Europe" for c in ["GB", "DE", "FR", "IT", "ES", "NL", "BE", "SE", "NO", "DK", "FI", "PL", "PT", "IE", "AT", "CH", "GR", "CZ", "RO", "HU", "UA", "RU", "BY", "RS", "BG", "HR", "SK", "SI", "LT", "LV", "EE"]},
    **{c: "Asia" for c in ["CN", "IN", "JP", "KR", "KP", "VN", "TH", "ID", "PH", "MY", "SG", "PK", "BD", "IR", "IQ", "SA", "AE", "IL", "TR", "TW", "HK", "KZ", "UZ", "NP", "LK", "MM", "KH", "LA", "MN"]},
    **{c: "Africa" for c in ["NG", "EG", "ZA", "KE", "ET", "GH", "DZ", "MA", "TN", "LY", "SD", "UG", "TZ", "CM", "CI", "SN", "ZW", "AO", "MZ"]},
    **{c: "Oceania" for c in ["AU", "NZ", "FJ", "PG"]},
}

_BUCKET_COLORS = ["#1a2332", "#2e4a6b", "#3d6ea5", "#4f96db", "#6fc3ff"]  # 0 (no data) -> 4 (highest)

TILE = 34
GAP = 3


def _bucket(count: int, max_count: int) -> int:
    """0 for no data at all; otherwise 1-4 by share of this render's own
    max (not a fixed absolute scale -- a quiet week and a busy week should
    both show visible contrast rather than everything reading as "low"
    against a scale sized for the busiest day this project ever has)."""
    if count <= 0 or max_count <= 0:
        return 0
    ratio = count / max_count
    if ratio > 0.75:
        return 4
    if ratio > 0.5:
        return 3
    if ratio > 0.25:
        return 2
    return 1


def _run_logs_insights_query(
    logs_client, log_group_names: list[str], query: str, start_ms: int, end_ms: int, max_wait_seconds: float = 8.0,
) -> list[dict]:
    """Synchronous StartQuery + poll GetQueryResults -- boto3 has no
    waiter for Logs Insights. Log groups are passed via StartQuery's own
    logGroupNames parameter, not embedded as SOURCE clauses in the query
    text -- unlike cloudwatch-dashboard-viewer-analytics.tf's own
    dashboard-widget queries (which have no separate field for multiple
    sources and so have to chain `SOURCE 'a' | SOURCE 'b'` inline), a
    direct StartQuery call has a real parameter for this, and using it
    avoids ever hand-building query-syntax text for log group names.
    Returns [] on a failed/cancelled/timed-out query or if
    max_wait_seconds elapses before completion -- a render with no data
    is far better than one that raises and shows nothing at all."""
    query_id = logs_client.start_query(
        logGroupNames=log_group_names, queryString=query, startTime=start_ms // 1000, endTime=end_ms // 1000,
    )["queryId"]
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        response = logs_client.get_query_results(queryId=query_id)
        status = response.get("status")
        if status == "Complete":
            return [{field["field"]: field["value"] for field in row} for row in response.get("results", [])]
        if status in ("Failed", "Cancelled", "Timeout"):
            return []
        time.sleep(0.5)
    return []


def _accepted_counts_by_state(logs_client, log_group_names: list[str], start_ms: int, end_ms: int) -> dict[str, int]:
    query = """
        filter @message like /viewer_analytics/
        | parse @message '"region": "*"' as region
        | stats count(*) as requests by region
    """
    rows = _run_logs_insights_query(logs_client, log_group_names, query, start_ms, end_ms)
    return {row["region"]: int(row["requests"]) for row in rows if row.get("region") in US_STATE_GRID}


def _blocked_counts_by_country(logs_client, log_group_name: str, start_ms: int, end_ms: int) -> dict[str, int]:
    query = """
        filter `sc-status` = "403"
        | stats count(*) as requests by `c-country`
    """
    rows = _run_logs_insights_query(logs_client, [log_group_name], query, start_ms, end_ms)
    return {row["c-country"]: int(row["requests"]) for row in rows if row.get("c-country")}


def _svg_style() -> str:
    return (
        "font-family:-apple-system,Segoe UI,sans-serif;font-size:10px;fill:#e6e6e6;"
        "text-anchor:middle;dominant-baseline:central;"
    )


def _render_state_grid(counts: dict[str, int]) -> str:
    max_count = max(counts.values(), default=0)
    max_col = max(col for _, col in US_STATE_GRID.values())
    max_row = max(row for row, _ in US_STATE_GRID.values())
    width = (max_col + 1) * (TILE + GAP)
    height = (max_row + 1) * (TILE + GAP)
    tiles = []
    for code, (row, col) in sorted(US_STATE_GRID.items()):
        count = counts.get(code, 0)
        color = _BUCKET_COLORS[_bucket(count, max_count)]
        x, y = col * (TILE + GAP), row * (TILE + GAP)
        tiles.append(
            f'<g><rect x="{x}" y="{y}" width="{TILE}" height="{TILE}" rx="3" fill="{color}" stroke="#0d1420" stroke-width="1">'
            f"<title>{code}: {count} request{'s' if count != 1 else ''}</title></rect>"
            f'<text x="{x + TILE / 2}" y="{y + TILE / 2}" style="{_svg_style()}">{code}</text></g>'
        )
    return f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}">{"".join(tiles)}</svg>'


def _render_region_grid(counts_by_country: dict[str, int]) -> str:
    by_region: dict[str, dict[str, int]] = {}
    for country, count in counts_by_country.items():
        region = COUNTRY_REGION.get(country, "Other")
        by_region.setdefault(region, {})[country] = count
    max_count = max(counts_by_country.values(), default=0)

    rows_html = []
    for region in sorted(by_region):
        tiles = []
        for country, count in sorted(by_region[region].items(), key=lambda kv: -kv[1]):
            color = _BUCKET_COLORS[_bucket(count, max_count)]
            tiles.append(
                f'<span title="{country}: {count} blocked" style="display:inline-block;width:{TILE}px;height:{TILE}px;'
                f"line-height:{TILE}px;margin:{GAP}px;border-radius:3px;background:{color};color:#e6e6e6;"
                f'font-family:-apple-system,Segoe UI,sans-serif;font-size:10px;text-align:center;">{country}</span>'
            )
        rows_html.append(
            f'<div style="margin-bottom:6px;"><div style="color:#9aa5b1;font-size:11px;margin-bottom:2px;">{region}</div>'
            f'<div>{"".join(tiles)}</div></div>'
        )
    return "".join(rows_html) or '<div style="color:#9aa5b1;">No blocked traffic in this time range.</div>'


def _describe() -> str:
    return (
        "## Geo widget\n\n"
        "Schematic tile-grid cartogram (not a geographic projection -- see handler.py's own docstring). "
        "Set the `mode` parameter to `accepted` (US states, shaded by accepted-request volume) "
        "or `blocked` (world regions, shaded by CloudFront-blocked-request volume)."
    )


def lambda_handler(event, context):
    if event.get("describe"):
        return _describe()

    mode = (event.get("mode") or "accepted").lower()
    widget_context = event.get("widgetContext") or {}
    time_range = widget_context.get("timeRange") or {}
    start_ms = time_range.get("start", 0)
    end_ms = time_range.get("end", 0)

    if mode == "blocked":
        # The CloudFront edge-access log group only ever exists in
        # us-east-1, regardless of this Lambda's own region -- same fixed
        # region cloudfront-standard-logging.tf's own resources use, for
        # the same reason (CloudFront-level log delivery is a us-east-1-only
        # API). A plain boto3.client("logs") here would default to this
        # Lambda's own execution region and find nothing.
        logs_client = boto3.client("logs", region_name="us-east-1")
        counts = _blocked_counts_by_country(logs_client, os.environ["BLOCKED_LOG_GROUP_NAME"], start_ms, end_ms)
        body = _render_region_grid(counts)
    else:
        # Every predict-read log group lives in this Lambda's own region
        # -- no explicit region_name needed, defaults to it already.
        logs_client = boto3.client("logs")
        log_group_names = os.environ["ACCEPTED_LOG_GROUP_NAMES"].split(",")
        counts = _accepted_counts_by_state(logs_client, log_group_names, start_ms, end_ms)
        body = _render_state_grid(counts)

    return f'<div style="background:#0d1420;padding:8px;border-radius:6px;">{body}</div>'
