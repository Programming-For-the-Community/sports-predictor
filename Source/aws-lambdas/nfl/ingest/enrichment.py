"""
Coach/injury/depth-chart enrichment for ingest/handler.py's scoreboard
writes -- see that module's own docstring for why this runs on every
ingest cycle and what's cached vs. fetched fresh. Split out of handler.py
to keep the main fetch loop focused on the scoreboard/box-score write
path.

enrich_events takes its S3 client and bucket explicitly rather than
holding its own, same convention predict/live_features.py and
model_loader.py use for their own dependencies.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

from botocore.exceptions import ClientError

from library.http.espn_core import EspnCoreApiClient
from library.http.nfl import NFLClient
from library.storage.depth_chart_cache import attach_depth_charts, home_away_team_ids

logger = logging.getLogger("nfl-ingest")

# Injuries are deliberately NOT cached (no TTL constant here), they're
# fetched fresh every run -- see this module's own docstring.
COACHES_CACHE_TTL_DAYS = 7  # tied to the weekly training cadence

_T = TypeVar("_T")


def _get_json(s3, bucket: str, key: str) -> dict | None:
    """None for a missing key (not yet cached) or a malformed cache entry
    -- either way the caller should treat it as a cache miss and fetch
    fresh, not raise."""
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read())
    except (ClientError, json.JSONDecodeError):
        return None


def _put_json(s3, bucket: str, key: str, payload: dict) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(payload), ContentType="application/json")


def _cached_or_fetch(s3, bucket: str, key: str, ttl_days: int, fetch: Callable[[], _T]) -> _T:
    """Returns the value cached at `key` if it was fetched within the last
    `ttl_days`, otherwise calls `fetch()`, caches the result (wrapped with
    a fetched_at timestamp so the next call can judge its own age), and
    returns it. Used for coach/depth-chart data (see this module's own
    docstring for the ESPN-call-volume reasoning) -- deliberately NOT used
    for injuries, which need to be fetched fresh every run regardless of
    any cache.

    A fetch failure propagates to the caller rather than falling back to
    a stale cache entry -- matches enrich_events' existing best-effort
    handling (the field is simply omitted for that run), and avoids ever
    silently serving data stale enough to have missed its own TTL twice
    over."""
    cached = _get_json(s3, bucket, key)
    if cached is not None:
        fetched_at = datetime.fromisoformat(cached["fetched_at"])
        if datetime.now(timezone.utc) - fetched_at < timedelta(days=ttl_days):
            return cached["data"]

    data = fetch()
    _put_json(s3, bucket, key, {"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data})
    return data


def _coaches_cache_key(season: int) -> str:
    return f"nfl/cache/season-coaches/{season}.json"


def get_cached_coaches(s3, bucket: str, core_client: EspnCoreApiClient, season: int) -> dict:
    """Every currently-listed team's head coach for `season`, keyed by
    team id -- TTL-cached (COACHES_CACHE_TTL_DAYS) in S3 under
    _coaches_cache_key(season). Shared by enrich_events below (attaches
    coaches to a specific week's events, whatever season that week
    happens to be) and ingest/handler.py's own unconditional daily call
    (seeds/refreshes the cache regardless of whether there's a week to
    enrich at all -- see that module's own docstring for why this can't
    rely on enrich_events alone) -- both hit the exact same cache key for
    the same season, so during the season this is a cache hit the second
    time either one runs that day, not a doubled ESPN cost."""
    return _cached_or_fetch(
        s3, bucket, _coaches_cache_key(season), COACHES_CACHE_TTL_DAYS,
        lambda: core_client.get_season_coaches(season),
    )


def enrich_events(
    events: list[dict], season: int, nfl_client: NFLClient, core_client: EspnCoreApiClient, s3, bucket: str,
) -> None:
    """Attaches home_coach/away_coach, home_injuries/away_injuries, and
    home_depth_chart/away_depth_chart onto each event dict in place,
    before the scoreboard payload is written to S3 -- see ingest/
    handler.py's own docstring for why this has to run on every ingest
    cycle rather than a lighter subset of them. Best-effort throughout: a
    coach/injury/depth-chart fetch failure is logged and that field is
    simply omitted, never allowed to take down the scoreboard write
    itself, which every other feature depends on regardless of this
    enrichment's success.

    Takes both clients (and the S3 client/bucket) as parameters rather
    than constructing its own -- lambda_handler already builds one
    NFLClient for the scoreboard/box score fetches, and a second,
    independent instantiation here would both duplicate that and, in
    tests, silently escape whatever mock a caller patched the
    module-level NFLClient/EspnCoreApiClient constructor with."""
    team_ids: set[str] = set()
    for event in events:
        ids = home_away_team_ids(event)
        if ids is not None:
            team_ids.update(ids)

    try:
        coaches = get_cached_coaches(s3, bucket, core_client, season)
    except Exception:
        logger.exception("Failed fetching season coaches for %s -- coach fields will be omitted", season)
        coaches = {}

    injuries_by_team: dict[str, list[dict]] = {}
    for team_id in team_ids:
        try:
            # Not cached, unlike coaches -- see this module's own
            # docstring for why injuries need daily freshness.
            injuries_by_team[team_id] = core_client.get_team_injuries(team_id)
        except Exception:
            logger.exception("Failed fetching injuries for team %s -- injuries field will be omitted", team_id)

    for event in events:
        ids = home_away_team_ids(event)
        if ids is None:
            continue
        home_id, away_id = ids
        event["home_coach"] = coaches.get(home_id)
        event["away_coach"] = coaches.get(away_id)
        event["home_injuries"] = injuries_by_team.get(home_id)
        event["away_injuries"] = injuries_by_team.get(away_id)

    attach_depth_charts(events, nfl_client, s3, bucket)
