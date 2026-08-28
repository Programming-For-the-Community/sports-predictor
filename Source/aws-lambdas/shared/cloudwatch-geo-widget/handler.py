"""
CloudWatch custom-widget Lambda for the viewer-analytics dashboard's two
geo panels (Terraform/cloudwatch-dashboard-viewer-analytics.tf). CloudWatch
strips every <script>/<iframe> tag from a custom widget's returned HTML
(no client-side rendering allowed at all), so a real map here means a
real image composited server-side, not an interactive one. Renders via
Amazon Location Service's GetStaticMap: a real basemap (streets/borders/
labels) with colored, labeled point markers overlaid, returned as a JPEG
and embedded as a base64 <img>. $0.04 per 1,000 requests, no separately
provisioned Map resource -- a standalone regional API action.

Two render modes, via the widget's `mode` custom-widget parameter:
  "accepted" -- accepted requests by US state, from every sport's
    predict-read log group's viewer_analytics line (library.serving.
    viewer_analytics's `region` field, a 2-letter code), one marker per
    state at its own real (approximate) geographic center.
  "blocked"  -- CloudFront-blocked (403) requests by country, from the
    CloudFront edge-access log group, grouped into world regions via
    COUNTRY_REGION, one marker per region at its own real center point.

Runs Logs Insights synchronously (StartQuery + poll GetQueryResults) on
every render, then one GetStaticMap call. A GetStaticMap failure (e.g. a
transient Location Service error) degrades to a plain text summary
instead of crashing the whole widget.
"""
import base64
import logging
import os
import time

import boto3

logger = logging.getLogger("cloudwatch-geo-widget")

# Approximate real geographic centers (lon, lat) -- state boundary shapes
# aren't embedded here, so this plots one colored, labeled point per
# state via GetStaticMap's own overlay instead. Close enough at this
# zoom to read as a real map; not surveyed-precision.
US_STATE_CENTROIDS = {
    "AL": (-86.8, 32.8), "AK": (-152.0, 64.0), "AZ": (-111.6, 34.3), "AR": (-92.4, 34.9),
    "CA": (-119.4, 36.8), "CO": (-105.5, 39.0), "CT": (-72.7, 41.6), "DE": (-75.5, 39.0),
    "DC": (-77.0, 38.9), "FL": (-81.6, 27.8), "GA": (-83.4, 32.6), "HI": (-155.5, 19.6),
    "ID": (-114.6, 44.2), "IL": (-89.2, 40.0), "IN": (-86.3, 39.9), "IA": (-93.5, 42.0),
    "KS": (-98.4, 38.5), "KY": (-84.6, 37.5), "LA": (-91.9, 31.0), "ME": (-69.4, 45.4),
    "MD": (-76.6, 39.0), "MA": (-71.5, 42.3), "MI": (-84.6, 44.3), "MN": (-94.6, 46.4),
    "MS": (-89.7, 32.7), "MO": (-92.5, 38.5), "MT": (-110.4, 47.0), "NE": (-99.9, 41.5),
    "NV": (-117.0, 39.5), "NH": (-71.5, 43.7), "NJ": (-74.5, 40.1), "NM": (-106.0, 34.4),
    "NY": (-75.5, 42.9), "NC": (-79.4, 35.6), "ND": (-100.5, 47.5), "OH": (-82.8, 40.4),
    "OK": (-97.5, 35.6), "OR": (-120.5, 44.0), "PA": (-77.6, 40.9), "RI": (-71.5, 41.7),
    "SC": (-80.9, 33.9), "SD": (-100.3, 44.4), "TN": (-86.7, 35.9), "TX": (-99.3, 31.5),
    "UT": (-111.7, 39.3), "VT": (-72.6, 44.0), "VA": (-78.8, 37.5), "WA": (-120.5, 47.4),
    "WV": (-80.6, 38.6), "WI": (-89.9, 44.6), "WY": (-107.3, 42.9),
}

# CONUS-only view -- AK/HI's own markers are still built into the overlay
# string (they just render off-frame); a world-scale box would shrink
# every CONUS state's marker down past legibility.
_CONUS_BOUNDING_BOX = "-125,24,-67,49"

# Not exhaustive. Anything unmapped falls into "Other" rather than being
# dropped -- see _blocked_map_overlay for why "Other" has no marker.
COUNTRY_REGION = {
    **{c: "Americas" for c in ["US", "CA", "MX", "BR", "AR", "CO", "CL", "PE", "VE", "CU", "DO", "GT", "EC", "BO", "PY", "UY", "CR", "PA", "JM", "HT", "TT"]},
    **{c: "Europe" for c in ["GB", "DE", "FR", "IT", "ES", "NL", "BE", "SE", "NO", "DK", "FI", "PL", "PT", "IE", "AT", "CH", "GR", "CZ", "RO", "HU", "UA", "RU", "BY", "RS", "BG", "HR", "SK", "SI", "LT", "LV", "EE"]},
    **{c: "Asia" for c in ["CN", "IN", "JP", "KR", "KP", "VN", "TH", "ID", "PH", "MY", "SG", "PK", "BD", "IR", "IQ", "SA", "AE", "IL", "TR", "TW", "HK", "KZ", "UZ", "NP", "LK", "MM", "KH", "LA", "MN"]},
    **{c: "Africa" for c in ["NG", "EG", "ZA", "KE", "ET", "GH", "DZ", "MA", "TN", "LY", "SD", "UG", "TZ", "CM", "CI", "SN", "ZW", "AO", "MZ"]},
    **{c: "Oceania" for c in ["AU", "NZ", "FJ", "PG"]},
}

# Rough real center points for each region bucket -- one marker per
# bucket, not per country (COUNTRY_REGION already only groups into these
# 5, same abstraction level as before, now placed on a real map).
REGION_CENTROIDS = {
    "Americas": (-90.0, 15.0),
    "Europe": (15.0, 50.0),
    "Asia": (90.0, 30.0),
    "Africa": (20.0, 5.0),
    "Oceania": (140.0, -25.0),
}
_WORLD_BOUNDING_BOX = "-170,-56,180,72"

_BUCKET_COLORS = ["#4a5568", "#2e4a6b", "#3d6ea5", "#4f96db", "#6fc3ff"]  # 0 (no data) -> 4 (highest)

_MAP_WIDTH = 600
_MAP_HEIGHT = 260


def _bucket(count: int, max_count: int) -> int:
    """0 for no data; otherwise 1-4 by share of this render's own max
    (not a fixed absolute scale)."""
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
    waiter for Logs Insights. Log groups are passed via StartQuery's
    logGroupNames parameter, not embedded as SOURCE clauses in the query
    text. Returns [] on a failed/cancelled/timed-out query or if
    max_wait_seconds elapses first."""
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
    return {row["region"]: int(row["requests"]) for row in rows if row.get("region") in US_STATE_CENTROIDS}


def _blocked_counts_by_country(logs_client, log_group_name: str, start_ms: int, end_ms: int) -> dict[str, int]:
    query = """
        filter `sc-status` = "403"
        | stats count(*) as requests by `c-country`
    """
    rows = _run_logs_insights_query(logs_client, [log_group_name], query, start_ms, end_ms)
    return {row["c-country"]: int(row["requests"]) for row in rows if row.get("c-country")}


def _compact_overlay_points(points: list[tuple[str, float, float, str]]) -> str:
    """points: (label, lon, lat, hex_color). Builds GetStaticMap's
    CompactOverlay format: one `point:lon,lat;label=..;color=..` term per
    point, pipe-separated."""
    return "|".join(f"point:{lon},{lat};label={label};color={color}" for label, lon, lat, color in points)


def _accepted_map_overlay(counts: dict[str, int]) -> str:
    max_count = max(counts.values(), default=0)
    points = [
        (code, lon, lat, _BUCKET_COLORS[_bucket(counts.get(code, 0), max_count)])
        for code, (lon, lat) in sorted(US_STATE_CENTROIDS.items())
    ]
    return _compact_overlay_points(points)


def _blocked_map_overlay(counts_by_country: dict[str, int]) -> str:
    by_region: dict[str, int] = {}
    for country, count in counts_by_country.items():
        region = COUNTRY_REGION.get(country, "Other")
        by_region[region] = by_region.get(region, 0) + count
    # "Other" has no fixed real location -- its own count still lands in
    # by_region (so it isn't silently lost from the underlying data), it
    # just never gets a marker on the map.
    max_count = max(by_region.values(), default=0)
    points = [
        (region, lon, lat, _BUCKET_COLORS[_bucket(by_region.get(region, 0), max_count)])
        for region, (lon, lat) in sorted(REGION_CENTROIDS.items())
    ]
    return _compact_overlay_points(points)


def _get_static_map(geo_maps_client, bounding_box: str, overlay: str) -> bytes:
    """Amazon Location Service's GetStaticMap -- a real composited map
    image (Standard style, Dark color scheme to match the dashboard) with
    colored/labeled point markers baked in server-side. No separate Map
    resource to provision -- a standalone regional API action."""
    response = geo_maps_client.get_static_map(
        FileName="map",
        Width=_MAP_WIDTH,
        Height=_MAP_HEIGHT,
        BoundingBox=bounding_box,
        Style="Standard",
        ColorScheme="Dark",
        CompactOverlay=overlay,
    )
    return response["Blob"].read()


def _map_html(geo_maps_client, bounding_box: str, overlay: str, counts: dict[str, int]) -> str:
    """The composited map as a base64 <img>, or a plain-text fallback
    (never a crash/blank widget) if GetStaticMap itself fails."""
    try:
        image_bytes = _get_static_map(geo_maps_client, bounding_box, overlay)
    except Exception:
        logger.exception("GetStaticMap failed -- falling back to a text summary")
        summary = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items(), key=lambda kv: -kv[1])[:10])
        return f'<div style="color:#9aa5b1;">Map unavailable. Top counts -- {summary or "no data in this time range"}</div>'
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f'<img src="data:image/jpeg;base64,{encoded}" width="100%" alt="geo traffic map" />'


def _describe() -> str:
    return (
        "## Geo widget\n\n"
        "A real map (Amazon Location Service GetStaticMap) with colored, labeled markers. "
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

    # GetStaticMap isn't available in every region -- pinned to us-east-1
    # regardless of this Lambda's own region, same reasoning the
    # CloudFront edge-logs client below already uses.
    geo_maps_client = boto3.client("geo-maps", region_name="us-east-1")

    if mode == "blocked":
        # The CloudFront edge-access log group only ever exists in
        # us-east-1, regardless of this Lambda's own region.
        logs_client = boto3.client("logs", region_name="us-east-1")
        counts = _blocked_counts_by_country(logs_client, os.environ["BLOCKED_LOG_GROUP_NAME"], start_ms, end_ms)
        body = _map_html(geo_maps_client, _WORLD_BOUNDING_BOX, _blocked_map_overlay(counts), counts)
    else:
        logs_client = boto3.client("logs")
        log_group_names = os.environ["ACCEPTED_LOG_GROUP_NAMES"].split(",")
        counts = _accepted_counts_by_state(logs_client, log_group_names, start_ms, end_ms)
        body = _map_html(geo_maps_client, _CONUS_BOUNDING_BOX, _accepted_map_overlay(counts), counts)

    return f'<div style="background:#0d1420;padding:8px;border-radius:6px;">{body}</div>'
