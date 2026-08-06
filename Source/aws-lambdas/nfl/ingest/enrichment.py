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

logger = logging.getLogger("nfl-ingest")

# Depth chart positions leader-selection actually needs (see
# aws-lambdas/nfl/predict/live_features.py) -- not the full ~25-position
# payload ESPN returns, most of which (offensive line, special teams,
# individual defensive line spots) this project never predicts for.
DEPTH_CHART_POSITIONS = {"QB", "RB", "WR"}

# Injuries are deliberately NOT cached (no TTL constant here), they're
# fetched fresh every run -- see this module's own docstring.
COACHES_CACHE_TTL_DAYS = 7  # tied to the weekly training cadence
DEPTH_CHART_CACHE_TTL_DAYS = 3

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


def home_away_team_ids(event: dict) -> tuple[str, str] | None:
    """Same navigation path as scoreboard_event_to_event_item
    (library/normalize/espn.py) -- competitions[0].competitors[], each
    carrying its own team id and homeAway role. Returns None for a
    malformed event rather than raising, since enrichment is best-effort
    and shouldn't take down the whole ingest run over one bad event."""
    try:
        competitors = event["competitions"][0]["competitors"]
        home = next(c for c in competitors if c.get("homeAway") == "home")
        away = next(c for c in competitors if c.get("homeAway") == "away")
        return str(home["team"]["id"]), str(away["team"]["id"])
    except (KeyError, IndexError, StopIteration):
        return None


def filter_depth_chart(raw_depth_chart: dict) -> dict:
    """Keeps only the positions leader-selection actually needs (see
    DEPTH_CHART_POSITIONS), AND trims each retained athlete down to just
    the id live_features.py's _healthy_athlete_ids actually reads --
    ESPN's full depth chart response is ~289KB for ONE team, almost
    entirely per-athlete link metadata (player card, stats, splits, game
    log, news, bio -- each with a web AND a sportscenter:// deep-link
    variant) this project never uses. Filters on each entry's
    position.abbreviation rather than the outer dict key, since that
    key's exact casing/format is only verified for non-skill-position
    codes (e.g. "lde" for Left Defensive End)."""
    positions = raw_depth_chart.get("positions") or {}
    result = {}
    for code, entry in positions.items():
        abbreviation = (entry.get("position") or {}).get("abbreviation")
        if abbreviation not in DEPTH_CHART_POSITIONS:
            continue
        result[code] = {
            "position": {"abbreviation": abbreviation},
            "athletes": [{"id": athlete["id"]} for athlete in entry.get("athletes", []) if "id" in athlete],
        }
    return result


def _coaches_cache_key(season: int) -> str:
    return f"nfl/cache/season-coaches/{season}.json"


def _depth_chart_cache_key(team_id: str) -> str:
    return f"nfl/cache/depth-charts/{team_id}.json"


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
        coaches = _cached_or_fetch(
            s3, bucket, _coaches_cache_key(season), COACHES_CACHE_TTL_DAYS,
            lambda: core_client.get_season_coaches(season),
        )
    except Exception:
        logger.exception("Failed fetching season coaches for %d -- coach fields will be omitted", season)
        coaches = {}

    injuries_by_team: dict[str, list[dict]] = {}
    depth_chart_by_team: dict[str, dict] = {}
    for team_id in team_ids:
        try:
            # Not cached, unlike coaches/depth-charts below -- see this
            # module's own docstring for why injuries need daily freshness.
            injuries_by_team[team_id] = core_client.get_team_injuries(team_id)
        except Exception:
            logger.exception("Failed fetching injuries for team %s -- injuries field will be omitted", team_id)
        try:
            depth_chart_by_team[team_id] = _cached_or_fetch(
                s3, bucket, _depth_chart_cache_key(team_id), DEPTH_CHART_CACHE_TTL_DAYS,
                lambda team_id=team_id: filter_depth_chart(nfl_client.get_depth_chart(team_id)),
            )
        except Exception:
            logger.exception("Failed fetching depth chart for team %s -- depth chart field will be omitted", team_id)

    for event in events:
        ids = home_away_team_ids(event)
        if ids is None:
            continue
        home_id, away_id = ids
        event["home_coach"] = coaches.get(home_id)
        event["away_coach"] = coaches.get(away_id)
        event["home_injuries"] = injuries_by_team.get(home_id)
        event["away_injuries"] = injuries_by_team.get(away_id)
        event["home_depth_chart"] = depth_chart_by_team.get(home_id)
        event["away_depth_chart"] = depth_chart_by_team.get(away_id)
