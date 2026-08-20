"""
NFL ingest Lambda. Fetches that week's scoreboard and completed box
scores from ESPN and writes raw JSON to S3. The normalize Lambda is
triggered automatically by the resulting S3 PutObject events, so this
function never touches DynamoDB directly.

Also enriches every event in that scoreboard payload with each
participating team's current head coach, injury report, and depth chart
(see enrichment.enrich_events) before writing it, embedded directly into
the same scoreboard JSON rather than a separate S3 object.

Also fetches every one of the league's 32 teams' current full roster
(not just the depth chart's skill-position subset) and writes it to its
own S3 object per team, on every run, uncached. Runs unconditionally,
before the preseason check below and independent of which teams have a
game this week. normalize picks each one up via the same S3 PutObject
trigger as everything else here and corrects that team's players' entity
records (library.normalize.espn.roster_to_player_entities).

Also refreshes every one of the league's 32 teams' cached depth chart
(library.storage.depth_chart_cache), same unconditional every-team,
every-run cadence as the roster fetch above. This one is cache-backed
(get_cached_depth_chart's own DEPTH_CHART_CACHE_TTL_DAYS).

Coach data is cached in S3 with its own TTL (see
enrichment.COACHES_CACHE_TTL_DAYS) -- "every ingest run" above is about
what gets WRITTEN each time (always the complete picture), not what gets
FETCHED from ESPN each time.

Also refreshes the season-coaches cache (_fetch_coaches) for the current
-- or, off-season, upcoming -- season, same unconditional every-run
cadence as the roster/depth-chart fetches above, run before the
preseason check below. get_cached_coaches' own TTL (COACHES_CACHE_TTL_DAYS
above) does the rate limiting.

EventBridge can override any default via the schedule's input payload:
    { "season": 2025, "season_type": 2, "week": 4 }

All three must be given together for a manual override -- there's no
partial-override path. Omitting all three auto-detects the target week
from the most recent Sunday's date: ESPN's scoreboard endpoint resolves
season/type/week entirely from a `dates=YYYYMMDD` param.

Future weeks of the current season are seeded separately by the
dedicated nfl-schedule-sync Lambda (aws-lambdas/nfl/schedule-sync/
handler.py), which writes scoreboard JSON directly to the same S3 key
pattern this function does, skipping the enrichment below.

Preseason (season_type 1) scoreboard/box-score/enrichment data is never
ingested, whether auto-detected or passed explicitly. Roster fetching
(above) is the one exception -- it runs before this check, every day
regardless of season_type.
"""
import json
import logging
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

import enrichment
from library.http.espn_core import EspnCoreApiClient
from library.http.nfl import NFLClient
from library.storage.depth_chart_cache import get_cached_depth_chart

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nfl-ingest")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]
PRESEASON_TYPE = 1

_s3 = boto3.client("s3")


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


def _all_team_ids(client: NFLClient) -> list[str]:
    """Every one of the league's 32 team ids, via get_teams -- not derived
    from any week's scoreboard, so this works identically in preseason,
    on a bye week, or with no games ingested at all."""
    teams_response = client.get_teams()
    leagues = teams_response.get("sports", [{}])[0].get("leagues", [{}])
    teams = leagues[0].get("teams", []) if leagues else []
    return [t["team"]["id"] for t in teams if t.get("team", {}).get("id")]


def _fetch_rosters(client: NFLClient) -> tuple[int, int]:
    """Fetches and writes every NFL team's current roster -- one S3
    object per team, always fresh, never TTL-cached. Best-effort per
    team -- one team's fetch failing doesn't stop the others."""
    fetched = failed = 0
    for team_id in _all_team_ids(client):
        try:
            roster = client.get_roster(team_id)
            _put_json(f"nfl/roster/{team_id}.json", roster)
            fetched += 1
        except Exception:
            logger.exception("Failed fetching roster for team %s", team_id)
            failed += 1
    return fetched, failed


def _current_nfl_season(today: date | None = None) -> int:
    """Sep-Dec resolves to this year (the season in progress), Jan-Feb
    resolves to last year (still finishing that season's playoffs -- the
    Super Bowl is in February), Mar-Aug resolves to this year (the
    upcoming, not-yet-started season)."""
    today = today or date.today()
    return today.year if today.month >= 3 else today.year - 1


def _fetch_coaches(core_client: EspnCoreApiClient) -> bool:
    """Refreshes the season-coaches cache for the current (in season) or
    upcoming (off-season) season. enrichment.get_cached_coaches' own TTL
    does the rate limiting."""
    season = _current_nfl_season()
    try:
        enrichment.get_cached_coaches(_s3, RAW_BUCKET, core_client, season)
        return True
    except Exception:
        logger.exception("Failed fetching season coaches for %d", season)
        return False


def _fetch_depth_charts(client: NFLClient) -> tuple[int, int]:
    """Refreshes every NFL team's cached depth chart (library.storage.
    depth_chart_cache). get_cached_depth_chart's own TTL
    (DEPTH_CHART_CACHE_TTL_DAYS) does the rate limiting. Best-effort per
    team, same convention as _fetch_rosters."""
    fetched = failed = 0
    for team_id in _all_team_ids(client):
        try:
            get_cached_depth_chart(_s3, RAW_BUCKET, client, team_id)
            fetched += 1
        except Exception:
            logger.exception("Failed fetching depth chart for team %s", team_id)
            failed += 1
    return fetched, failed


def lambda_handler(event: dict, context) -> dict:
    season = event.get("season")
    season_type = event.get("season_type")
    week = event.get("week")

    client = NFLClient()
    core_client = EspnCoreApiClient()

    # Runs unconditionally, before the preseason check below.
    rosters_fetched, rosters_failed = _fetch_rosters(client)
    logger.info("Rosters: %d fetched, %d failed", rosters_fetched, rosters_failed)

    depth_charts_fetched, depth_charts_failed = _fetch_depth_charts(client)
    logger.info("Depth charts: %d fetched, %d failed", depth_charts_fetched, depth_charts_failed)

    coaches_fetched = _fetch_coaches(core_client)
    logger.info("Coaches: %s", "fetched" if coaches_fetched else "failed")

    if season_type == PRESEASON_TYPE:
        logger.info("season_type=%d is preseason -- skipping, not ingested by design", PRESEASON_TYPE)
        return {
            "processed": 0, "skipped": 0, "failed": 0,
            "rosters_fetched": rosters_fetched, "rosters_failed": rosters_failed,
            "depth_charts_fetched": depth_charts_fetched, "depth_charts_failed": depth_charts_failed,
            "coaches_fetched": coaches_fetched,
        }

    if week is None:
        # Season year/type live under leagues[0].season, and type is a
        # dict ({"id": "2", "type": 2, ...}) rather than a bare int.
        # week is top-level.
        scoreboard = client.get_scoreboard_for_date(_most_recent_sunday())
        league_season = (scoreboard.get("leagues") or [{}])[0].get("season", {})
        season = league_season.get("year", season)
        season_type = league_season.get("type", {}).get("type", season_type)
        week = scoreboard.get("week", {}).get("number", 1)

        if season_type == PRESEASON_TYPE:
            logger.info("Auto-detected preseason (season %s) -- skipping, not ingested by design", season)
            return {
                "processed": 0, "skipped": 0, "failed": 0,
                "rosters_fetched": rosters_fetched, "rosters_failed": rosters_failed,
                "depth_charts_fetched": depth_charts_fetched, "depth_charts_failed": depth_charts_failed,
                "coaches_fetched": coaches_fetched,
            }

        logger.info("Auto-detected season %s type %s week %s", season, season_type, week)
    else:
        scoreboard = client.get_scoreboard(season, season_type, week)

    events = scoreboard.get("events", [])
    logger.info("Found %d events in season %s type %s week %s", len(events), season, season_type, week)

    # Mutates each event dict in place -- scoreboard["events"] holds the
    # same list/dict objects, so this enrichment is already reflected in
    # `scoreboard` by the time it's written below.
    enrichment.enrich_events(events, season, client, core_client, _s3, RAW_BUCKET)

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
    return {
        "processed": processed, "skipped": skipped, "failed": failed,
        "rosters_fetched": rosters_fetched, "rosters_failed": rosters_failed,
        "depth_charts_fetched": depth_charts_fetched, "depth_charts_failed": depth_charts_failed,
        "coaches_fetched": coaches_fetched,
    }
