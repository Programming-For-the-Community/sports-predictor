"""
NFL ingest Lambda. Triggered by EventBridge Scheduler -- see
Terraform/scheduler-nfl-ingest.tf, which runs this daily during the
season. Fetches that week's scoreboard and completed box scores from
ESPN and writes raw JSON to S3. The normalize Lambda is triggered
automatically by the resulting S3 PutObject events, so this function
never touches DynamoDB directly.

Also enriches every event in that scoreboard payload with each
participating team's current head coach, injury report, and depth chart
(see _enrich_events) before writing it -- embedded directly into the
same scoreboard JSON, not a separate S3 object, both to keep the write
atomic (normalize's _process_scoreboard reads it all from one place) and
because scoreboard_event_to_event_item (library/normalize/espn.py)
rebuilds the whole event item from scratch every time it runs -- so this
enrichment has to happen on EVERY ingest run, not a lighter subset of
runs, or a run that omitted it would silently wipe out a previous run's
coach/injury/depth-chart fields on the next normalize rebuild. This is
also why ingest runs daily now instead of just Tue/Wed: injury reports
change through the week in a way box scores and coach data don't.

Coach and depth-chart data are cached in S3 with their own TTLs (see
_cached_or_fetch, COACHES_CACHE_TTL_DAYS/DEPTH_CHART_CACHE_TTL_DAYS) --
"every ingest run" above is about what gets WRITTEN each time (always the
complete picture, for the reason above), not what gets FETCHED from ESPN
each time. get_season_coaches alone costs ~65 ESPN calls (ESPN's core API
pages this via two rounds of $ref resolution, listing -> 32 coach details
-> 32 separate win-record lookups) for data that barely changes day to
day -- a coach's identity/tenure almost never changes mid-week, and
season_win_pct only changes once a week, after that week's games. Depth
charts (32 calls/day, one per team playing that week) shift with roster
moves, but not meaningfully within a single day either, and
injury-driven changes are already separately captured by the (uncached,
genuinely-daily) injuries call.

EventBridge can override any default via the schedule's input payload:
    { "season": 2025, "season_type": 2, "week": 4 }

All three must be given together for a manual override (e.g. reprocessing
one specific past week) -- there's no partial-override path. Omitting all
three (the normal scheduled case) auto-detects the target week from the
most recent Sunday's date rather than from "today": ESPN's scoreboard
endpoint resolves season/type/week entirely from a `dates=YYYYMMDD` param
(confirmed live), and "most recent Sunday" is the same value on both the
Tuesday run and the Wednesday retry within the same NFL week, unlike
"today" -- which would otherwise depend on exactly when ESPN's own
scoreboard calendar rolls over to the next week, an undocumented detail
this function has no business depending on.

Future weeks of the current season are seeded separately by
Terraform/scheduler-nfl-schedule-sync.tf's dedicated nfl-schedule-sync
Lambda (aws-lambdas/nfl/schedule-sync/handler.py), which writes scoreboard
JSON directly to the same S3 key pattern this function does -- not routed
through this handler, since that job deliberately skips the enrichment
below (meaningless months ahead of a game) and needs one shared rate
limiter across ~23 ESPN calls, not 23 separate invocations each with
their own.

Preseason (season_type 1) is never ingested, whether auto-detected or
passed explicitly -- backup-heavy preseason rosters and results aren't
representative of regular-season performance and would skew training data.
This matches the historical backfill, which only ever pulls regular season
and postseason (see SEASON_TYPES in data-backfills/nfl/backfill.py).
"""
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Callable, TypeVar

import boto3
from botocore.exceptions import ClientError

from library.http.espn_core import EspnCoreApiClient
from library.http.nfl import NFLClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-ingest")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]
PRESEASON_TYPE = 1
# Depth chart positions leader-selection actually needs (see
# aws-lambdas/nfl/predict/live_features.py) -- not the full ~25-position
# payload ESPN returns, most of which (offensive line, special teams,
# individual defensive line spots) this project never predicts for.
DEPTH_CHART_POSITIONS = {"QB", "RB", "WR"}

# See _cached_or_fetch and this module's own docstring -- injuries are
# deliberately NOT cached (no TTL constant here), they're fetched fresh
# every run.
COACHES_CACHE_TTL_DAYS = 7  # tied to the weekly training cadence
DEPTH_CHART_CACHE_TTL_DAYS = 3

_s3 = boto3.client("s3")
_T = TypeVar("_T")


def _most_recent_sunday(today: date | None = None) -> str:
    """The most recent Sunday on or before `today` (default: the actual
    current date), as YYYYMMDD. date.weekday() is Monday=0..Sunday=6, so
    days-since-Sunday is (weekday + 1) % 7."""
    today = today or date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    return (today - timedelta(days=days_since_sunday)).strftime("%Y%m%d")


def _object_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=RAW_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(
        Bucket=RAW_BUCKET,
        Key=key,
        Body=json.dumps(payload),
        ContentType="application/json",
    )
    logger.info("Wrote s3://%s/%s", RAW_BUCKET, key)


def _get_json(key: str) -> dict | None:
    """None for a missing key (not yet cached) or a malformed cache entry
    -- either way the caller should treat it as a cache miss and fetch
    fresh, not raise."""
    try:
        response = _s3.get_object(Bucket=RAW_BUCKET, Key=key)
        return json.loads(response["Body"].read())
    except (ClientError, json.JSONDecodeError):
        return None


def _cached_or_fetch(key: str, ttl_days: int, fetch: Callable[[], _T]) -> _T:
    """Returns the value cached at `key` if it was fetched within the last
    `ttl_days`, otherwise calls `fetch()`, caches the result (wrapped with
    a fetched_at timestamp so the next call can judge its own age), and
    returns it. Used for coach/depth-chart data (see this module's own
    docstring for the ESPN-call-volume reasoning) -- deliberately NOT used
    for injuries, which need to be fetched fresh every run regardless of
    any cache.

    A fetch failure propagates to the caller rather than falling back to
    a stale cache entry -- matches _enrich_events' existing best-effort
    handling (the field is simply omitted for that run), and avoids ever
    silently serving data stale enough to have missed its own TTL twice
    over."""
    cached = _get_json(key)
    if cached is not None:
        fetched_at = datetime.fromisoformat(cached["fetched_at"])
        if datetime.now(timezone.utc) - fetched_at < timedelta(days=ttl_days):
            return cached["data"]

    data = fetch()
    _put_json(key, {"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data})
    return data


def _home_away_team_ids(event: dict) -> tuple[str, str] | None:
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


def _filter_depth_chart(raw_depth_chart: dict) -> dict:
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


def _enrich_events(events: list[dict], season: int, nfl_client: NFLClient, core_client: EspnCoreApiClient) -> None:
    """Attaches home_coach/away_coach, home_injuries/away_injuries, and
    home_depth_chart/away_depth_chart onto each event dict in place,
    before the scoreboard payload is written to S3 -- see this module's
    own docstring for why this has to run on every ingest cycle rather
    than a lighter subset of them. Best-effort throughout: a coach/
    injury/depth-chart fetch failure is logged and that field is simply
    omitted, never allowed to take down the scoreboard write itself,
    which every other feature depends on regardless of this enrichment's
    success.

    Takes both clients as parameters rather than constructing its own --
    lambda_handler already builds one NFLClient for the scoreboard/box
    score fetches above, and a second, independent instantiation here
    would both duplicate that and, in tests, silently escape whatever
    mock a caller patched the module-level NFLClient/EspnCoreApiClient
    constructor with."""
    team_ids: set[str] = set()
    for event in events:
        ids = _home_away_team_ids(event)
        if ids is not None:
            team_ids.update(ids)

    try:
        coaches = _cached_or_fetch(
            _coaches_cache_key(season), COACHES_CACHE_TTL_DAYS, lambda: core_client.get_season_coaches(season),
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
                _depth_chart_cache_key(team_id), DEPTH_CHART_CACHE_TTL_DAYS,
                lambda team_id=team_id: _filter_depth_chart(nfl_client.get_depth_chart(team_id)),
            )
        except Exception:
            logger.exception("Failed fetching depth chart for team %s -- depth chart field will be omitted", team_id)

    for event in events:
        ids = _home_away_team_ids(event)
        if ids is None:
            continue
        home_id, away_id = ids
        event["home_coach"] = coaches.get(home_id)
        event["away_coach"] = coaches.get(away_id)
        event["home_injuries"] = injuries_by_team.get(home_id)
        event["away_injuries"] = injuries_by_team.get(away_id)
        event["home_depth_chart"] = depth_chart_by_team.get(home_id)
        event["away_depth_chart"] = depth_chart_by_team.get(away_id)


def lambda_handler(event: dict, context) -> dict:
    season = event.get("season")
    season_type = event.get("season_type")
    week = event.get("week")

    if season_type == PRESEASON_TYPE:
        logger.info("season_type=%d is preseason -- skipping, not ingested by design", PRESEASON_TYPE)
        return {"processed": 0, "skipped": 0, "failed": 0}

    client = NFLClient()
    core_client = EspnCoreApiClient()

    if week is None:
        # See the module docstring for why this resolves against the most
        # recent Sunday rather than "today" -- it's what makes the
        # Tuesday run and the Wednesday retry agree on the same week.
        scoreboard = client.get_scoreboard_for_date(_most_recent_sunday())
        season = scoreboard.get("season", {}).get("year", season)
        season_type = scoreboard.get("season", {}).get("type", season_type)
        week = scoreboard.get("week", {}).get("number", 1)

        if season_type == PRESEASON_TYPE:
            logger.info("Auto-detected preseason (season %d) -- skipping, not ingested by design", season)
            return {"processed": 0, "skipped": 0, "failed": 0}

        logger.info("Auto-detected season %d type %d week %d", season, season_type, week)
    else:
        scoreboard = client.get_scoreboard(season, season_type, week)

    events = scoreboard.get("events", [])
    logger.info("Found %d events in season %d type %d week %d", len(events), season, season_type, week)

    # Mutates each event dict in place -- scoreboard["events"] holds the
    # same list/dict objects, so this enrichment is already reflected in
    # `scoreboard` by the time it's written below. See this module's own
    # docstring for why this runs on every ingest cycle unconditionally.
    _enrich_events(events, season, client, core_client)

    scoreboard_key = f"nfl/scoreboard/{season}/{season_type}/{week}.json"
    _put_json(scoreboard_key, scoreboard)

    processed = skipped = failed = 0
    for evt in events:
        event_id = evt["id"]

        if not evt.get("status", {}).get("type", {}).get("completed", False):
            logger.debug("Skipping incomplete event %s", event_id)
            skipped += 1
            continue

        raw_key = f"nfl/boxscore/{season}/{event_id}.json"
        if _object_exists(raw_key):
            logger.debug("Box score already in S3, skipping event %s", event_id)
            skipped += 1
            continue

        try:
            summary = client.get_summary(event_id)
            _put_json(raw_key, summary)
            processed += 1
        except Exception:
            logger.exception("Failed fetching summary for event %s", event_id)
            failed += 1

    logger.info("Done: %d processed, %d skipped, %d failed", processed, skipped, failed)
    return {"processed": processed, "skipped": skipped, "failed": failed}