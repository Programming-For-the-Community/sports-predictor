"""Coach/injury/depth-chart enrichment attached to scoreboard events before
ingest/handler.py writes them to S3.
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

# Injuries have no TTL constant -- fetched fresh every run, never cached.
COACHES_CACHE_TTL_DAYS = 7

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
    a fetched_at timestamp), and returns it. A fetch failure propagates to
    the caller rather than falling back to a stale cache entry."""
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
    """Every currently-listed team's head coach for `season`, keyed by team
    id -- TTL-cached (COACHES_CACHE_TTL_DAYS) in S3 under
    _coaches_cache_key(season)."""
    return _cached_or_fetch(
        s3, bucket, _coaches_cache_key(season), COACHES_CACHE_TTL_DAYS,
        lambda: core_client.get_season_coaches(season),
    )


def enrich_events(
    events: list[dict], season: int, nfl_client: NFLClient, core_client: EspnCoreApiClient, s3, bucket: str,
) -> None:
    """Attaches home_coach/away_coach, home_injuries/away_injuries, and
    home_depth_chart/away_depth_chart onto each event dict in place.
    Best-effort throughout: a coach/injury/depth-chart fetch failure is
    logged and that field is simply omitted."""
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
