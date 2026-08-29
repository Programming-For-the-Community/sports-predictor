"""
CloudWatch custom-widget Lambda for the viewer-analytics dashboard's two
geo panels (Terraform/cloudwatch-dashboard-viewer-analytics.tf). CloudWatch
strips every <script>/<iframe> tag from a custom widget's returned HTML
(no client-side rendering allowed at all, so no MapLibre/heatmap-layer
style map is reachable here even though Amazon Location Service's own map
style spec supports one).

Renders the whole image locally with Pillow instead of calling a mapping
service: real state/country boundary lines (boundaries.json -- extracted
once, offline, from bokeh_sampledata's US state polygons and geopandas'
bundled Natural Earth lowres world shapefile, both real lon/lat data, no
topographic/terrain detail) drawn as thin reference strokes on a plain
dark canvas, then one small Gaussian-blurred glow per hotspot on top.
There's no external basemap image to align our own drawing against, so
this carries none of the projection-guessing risk a compositing
approach against a real map-tile API would.

Two render modes, via the widget's `mode` custom-widget parameter, each
matched to its own data's real granularity:
  "accepted" -- accepted requests by US state, from every sport's
    predict-read log group's viewer_analytics line (library.serving.
    viewer_analytics's `region` field, a 2-letter code). State is as
    fine as this data gets, so each state renders as a small, tight
    glow "pinpoint" at its own real (approximate) center rather than a
    filled shape.
  "blocked"  -- CloudFront-blocked (403) requests by country, from the
    CloudFront edge-access log group (`c-country`, a 2-letter code).
    Country is the real granularity here, so each country with nonzero
    traffic renders as its own filled shape (a real choropleth) instead
    of a pinpoint.

Runs Logs Insights synchronously (StartQuery + poll GetQueryResults) on
every render. A rendering failure degrades to a plain text summary
instead of crashing the whole widget.
"""
import base64
import io
import json
import logging
import os
import time
from pathlib import Path

import boto3
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger("cloudwatch-geo-widget")

with Path(__file__).with_name("boundaries.json").open() as _f:
    _BOUNDARIES = json.load(_f)
STATE_RINGS = {state["id"]: state["rings"] for state in _BOUNDARIES["states"]}
COUNTRY_RINGS = {country["id"]: country["rings"] for country in _BOUNDARIES["countries"]}

# Approximate real geographic centers (lon, lat) -- state boundary
# *shapes* come from STATE_RINGS; this is only used to place each
# state's own heat glow.
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

_MAP_WIDTH = 600
_MAP_HEIGHT = 260

# Everything is drawn at this multiple of the final size, then
# downscaled with a high-quality filter -- plain 1px-wide PIL lines and
# small circles have no antialiasing of their own, so without this the
# boundary strokes look jagged and the pinpoint glow's edge looks
# stair-stepped instead of smooth.
_SUPERSAMPLE = 3
_RENDER_WIDTH = _MAP_WIDTH * _SUPERSAMPLE
_RENDER_HEIGHT = _MAP_HEIGHT * _SUPERSAMPLE

# CONUS-only view, aspect-matched to _MAP_WIDTH/_MAP_HEIGHT so plain
# linear lon/lat -> pixel scaling (_project) doesn't visibly distort
# state shapes. AK/HI's own traffic is still counted, they just have no
# glow of their own off this frame.
_CONUS_BOUNDING_BOX = "-125,24,-67,49"

# Aspect-matched to _MAP_WIDTH/_MAP_HEIGHT so plain linear lon/lat ->
# pixel scaling (_project) doesn't visibly distort country shapes.
_WORLD_BOUNDING_BOX = "-131,-55,181,80"

_BACKGROUND_COLOR = (13, 20, 32)  # matches the widget's own wrapper div
_BOUNDARY_COLOR = (90, 104, 128)
_BOUNDARY_WIDTH_PX = 1  # at final resolution -- scaled by _SUPERSAMPLE when drawn

# Accepted-panel pinpoint appearance (final-resolution pixels, scaled by
# _SUPERSAMPLE when drawn) -- a small solid core per state,
# Gaussian-blurred into a tight glow, then colorized via
# _HEAT_COLOR_STOPS (blue, low, up to red, high). Deliberately tight --
# state is the real granularity of this data, so a pinpoint should read
# as one state, not spill into its neighbors. Core must stay comfortably
# above the blur radius or the blur dilutes a hotspot's own peak
# intensity below its intended color-stop threshold (verified: core=4/
# blur=6 washes a bucket-4 dot down to ~55/255, misreading as the
# lowest color; core=7/blur=2 keeps the peak at the full 255).
_HEAT_CORE_RADIUS_PX = 7
_HEAT_BLUR_RADIUS_PX = 2
_HEAT_COLOR_STOPS = [
    (0, (0, 0, 0, 0)),
    (70, (59, 130, 246, 190)),
    (140, (234, 179, 8, 220)),
    (200, (249, 115, 22, 240)),
    (255, (239, 68, 68, 255)),
]

_LEGEND_MARGIN_PX = 10
_LEGEND_BAR_WIDTH_PX = 120
_LEGEND_BAR_HEIGHT_PX = 10
_LEGEND_FONT_SIZE_PX = 11
_LEGEND_TEXT_COLOR = (154, 165, 177, 255)
_LEGEND_BACKGROUND_COLOR = (13, 20, 32, 215)


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


def _accepted_hotspots(counts: dict[str, int]) -> list[tuple[float, float, int]]:
    """(lon, lat, bucket) for every state with nonzero traffic."""
    max_count = max(counts.values(), default=0)
    hotspots = [
        (lon, lat, _bucket(counts.get(code, 0), max_count))
        for code, (lon, lat) in US_STATE_CENTROIDS.items()
    ]
    return [h for h in hotspots if h[2] > 0]


def _country_buckets(counts_by_country: dict[str, int]) -> dict[str, int]:
    """code -> bucket, countries with no traffic omitted entirely.
    Country is the real granularity of the blocked-traffic data, so
    there's no aggregation step here the way _accepted_hotspots doesn't
    need one either."""
    max_count = max(counts_by_country.values(), default=0)
    buckets = {code: _bucket(count, max_count) for code, count in counts_by_country.items()}
    return {code: bucket for code, bucket in buckets.items() if bucket > 0}


def _project(lon: float, lat: float, bounding_box: str, width: int, height: int) -> tuple[float, float]:
    """lon/lat -> pixel (x, y), plain linear scaling against the given
    bounding box. Nothing external to align against here -- boundary
    lines and heat overlays both go through this same projection, so
    they're always self-consistent regardless of its accuracy."""
    west, south, east, north = (float(v) for v in bounding_box.split(","))
    x = (lon - west) / (east - west) * width
    y = (north - lat) / (north - south) * height
    return x, y


def _draw_reference_boundaries(draw: ImageDraw.ImageDraw, rings_by_feature: list[list[list[list[float]]]], bounding_box: str) -> None:
    """rings_by_feature: one entry per state/country, each a list of
    closed [lon, lat] rings. Plain thin strokes -- no fill, no
    topography. Always drawn for every feature, regardless of traffic."""
    width = max(1, _BOUNDARY_WIDTH_PX * _SUPERSAMPLE)
    for rings in rings_by_feature:
        for ring in rings:
            points = [_project(lon, lat, bounding_box, _RENDER_WIDTH, _RENDER_HEIGHT) for lon, lat in ring]
            if len(points) >= 2:
                draw.line(points + [points[0]], fill=_BOUNDARY_COLOR, width=width)


def _interp_heat_color(value: int) -> tuple[int, int, int, int]:
    """Linear-interpolate an 8-bit intensity value through
    _HEAT_COLOR_STOPS."""
    for (t0, c0), (t1, c1) in zip(_HEAT_COLOR_STOPS, _HEAT_COLOR_STOPS[1:]):
        if t0 <= value <= t1:
            span = t1 - t0
            frac = (value - t0) / span if span else 0.0
            return tuple(int(c0[i] + (c1[i] - c0[i]) * frac) for i in range(4))
    return _HEAT_COLOR_STOPS[-1][1]


def _heat_color_lut() -> tuple[list[int], list[int], list[int], list[int]]:
    """256-entry per-channel lookup tables for Image.point, one list
    each for R/G/B/A."""
    colors = [_interp_heat_color(v) for v in range(256)]
    return tuple([c[i] for c in colors] for i in range(4))


def _pinpoint_layer(hotspots: list[tuple[float, float, int]], bounding_box: str) -> Image.Image:
    """Accepted panel only. A single-channel intensity map (one small
    solid circle per state, bucket-weighted), Gaussian-blurred into a
    tight glow, then colorized into an RGBA layer the same size as the
    render canvas."""
    density = Image.new("L", (_RENDER_WIDTH, _RENDER_HEIGHT), 0)
    draw = ImageDraw.Draw(density)
    r = _HEAT_CORE_RADIUS_PX * _SUPERSAMPLE
    for lon, lat, bucket in hotspots:
        x, y = _project(lon, lat, bounding_box, _RENDER_WIDTH, _RENDER_HEIGHT)
        intensity = int(255 * (bucket / 4.0))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=intensity)
    density = density.filter(ImageFilter.GaussianBlur(_HEAT_BLUR_RADIUS_PX * _SUPERSAMPLE))
    lut_r, lut_g, lut_b, lut_a = _heat_color_lut()
    return Image.merge("RGBA", (density.point(lut_r), density.point(lut_g), density.point(lut_b), density.point(lut_a)))


def _choropleth_layer(bucket_by_code: dict[str, int], bounding_box: str) -> Image.Image:
    """Blocked panel only. One semi-transparent filled shape per country
    with traffic -- country is the real granularity of this data, so no
    pinpoint/blur is needed, the country's own real shape carries the
    signal."""
    layer = Image.new("RGBA", (_RENDER_WIDTH, _RENDER_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for code, bucket in bucket_by_code.items():
        rings = COUNTRY_RINGS.get(code)
        if not rings:
            continue
        color = _interp_heat_color(int(255 * (bucket / 4.0)))
        for ring in rings:
            points = [_project(lon, lat, bounding_box, _RENDER_WIDTH, _RENDER_HEIGHT) for lon, lat in ring]
            if len(points) >= 3:
                draw.polygon(points, fill=color)
    return layer


def _legend_gradient(width: int, height: int) -> Image.Image:
    """A smooth blue -> red bar spanning only the real bucket range
    (1-4) -- excludes _HEAT_COLOR_STOPS' own bucket-0 (transparent
    black) stop, which would otherwise show as a dead black patch at
    the "Low" end instead of a clean gradient."""
    colors = [_interp_heat_color(int(255 * (bucket / 4.0)))[:3] for bucket in (1, 2, 3, 4)]
    strip = Image.new("RGB", (len(colors), 1))
    strip.putdata(colors)
    return strip.resize((width, height), Image.BILINEAR)


def _draw_legend(image: Image.Image) -> None:
    """Drawn directly on the final (already downscaled) image -- PIL's
    TrueType text rendering is already antialiased at normal scale, no
    need to supersample it too."""
    draw = ImageDraw.Draw(image, "RGBA")
    font = ImageFont.load_default(size=_LEGEND_FONT_SIZE_PX)
    title = "TRAFFIC VOLUME"
    title_height = _LEGEND_FONT_SIZE_PX + 4
    bar_x = _LEGEND_MARGIN_PX
    bar_y = image.height - _LEGEND_MARGIN_PX - _LEGEND_BAR_HEIGHT_PX - _LEGEND_FONT_SIZE_PX - 4

    pad = 6
    box = [
        bar_x - pad, bar_y - pad - title_height,
        bar_x + _LEGEND_BAR_WIDTH_PX + pad, bar_y + _LEGEND_BAR_HEIGHT_PX + _LEGEND_FONT_SIZE_PX + 4 + pad,
    ]
    draw.rounded_rectangle(box, radius=6, fill=_LEGEND_BACKGROUND_COLOR)
    draw.text((bar_x, bar_y - pad - title_height + 2), title, font=font, fill=_LEGEND_TEXT_COLOR)

    gradient = _legend_gradient(_LEGEND_BAR_WIDTH_PX, _LEGEND_BAR_HEIGHT_PX)
    image.paste(gradient, (bar_x, bar_y))

    label_y = bar_y + _LEGEND_BAR_HEIGHT_PX + 2
    draw.text((bar_x, label_y), "Low", font=font, fill=_LEGEND_TEXT_COLOR)
    high_width = draw.textlength("High", font=font)
    draw.text((bar_x + _LEGEND_BAR_WIDTH_PX - high_width, label_y), "High", font=font, fill=_LEGEND_TEXT_COLOR)


def _encode_jpeg(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _downscale(image: Image.Image) -> Image.Image:
    return image.resize((_MAP_WIDTH, _MAP_HEIGHT), Image.LANCZOS)


def _render_accepted_image(counts: dict[str, int]) -> bytes:
    canvas = Image.new("RGB", (_RENDER_WIDTH, _RENDER_HEIGHT), _BACKGROUND_COLOR)
    _draw_reference_boundaries(ImageDraw.Draw(canvas), list(STATE_RINGS.values()), _CONUS_BOUNDING_BOX)
    pinpoints = _pinpoint_layer(_accepted_hotspots(counts), _CONUS_BOUNDING_BOX)
    composited = Image.alpha_composite(canvas.convert("RGBA"), pinpoints).convert("RGB")
    final = _downscale(composited)
    _draw_legend(final)
    return _encode_jpeg(final)


def _render_blocked_image(counts_by_country: dict[str, int]) -> bytes:
    canvas = Image.new("RGB", (_RENDER_WIDTH, _RENDER_HEIGHT), _BACKGROUND_COLOR)
    _draw_reference_boundaries(ImageDraw.Draw(canvas), list(COUNTRY_RINGS.values()), _WORLD_BOUNDING_BOX)
    choropleth = _choropleth_layer(_country_buckets(counts_by_country), _WORLD_BOUNDING_BOX)
    composited = Image.alpha_composite(canvas.convert("RGBA"), choropleth).convert("RGB")
    final = _downscale(composited)
    _draw_legend(final)
    return _encode_jpeg(final)


def _map_html(render_fn, counts: dict[str, int]) -> str:
    """The rendered map as a base64 <img>, or a plain-text fallback
    (never a crash/blank widget) if rendering itself fails."""
    try:
        image_bytes = render_fn(counts)
    except Exception:
        logger.exception("Map rendering failed -- falling back to a text summary")
        summary = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items(), key=lambda kv: -kv[1])[:10])
        return f'<div style="color:#9aa5b1;">Map unavailable. Top counts -- {summary or "no data in this time range"}</div>'
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f'<img src="data:image/jpeg;base64,{encoded}" width="100%" alt="geo traffic map" />'


def _describe() -> str:
    return (
        "## Geo widget\n\n"
        "A locally-rendered map (real state/country boundary lines, no topography). Set the `mode` "
        "parameter to `accepted` (US states, small pinpoint glow by accepted-request volume) or "
        "`blocked` (world countries, filled by CloudFront-blocked-request volume)."
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
        # us-east-1, regardless of this Lambda's own region.
        logs_client = boto3.client("logs", region_name="us-east-1")
        counts = _blocked_counts_by_country(logs_client, os.environ["BLOCKED_LOG_GROUP_NAME"], start_ms, end_ms)
        body = _map_html(_render_blocked_image, counts)
    else:
        logs_client = boto3.client("logs")
        log_group_names = os.environ["ACCEPTED_LOG_GROUP_NAMES"].split(",")
        counts = _accepted_counts_by_state(logs_client, log_group_names, start_ms, end_ms)
        body = _map_html(_render_accepted_image, counts)

    return f'<div style="background:#0d1420;padding:8px;border-radius:6px;">{body}</div>'
