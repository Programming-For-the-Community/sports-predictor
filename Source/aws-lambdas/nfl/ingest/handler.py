"""
NFL ingest Lambda. Triggered by EventBridge Scheduler -- see
Terraform/scheduler-nfl-ingest.tf, which runs this daily during the
season. Fetches that week's scoreboard and completed box scores from
ESPN and writes raw JSON to S3. The normalize Lambda is triggered
automatically by the resulting S3 PutObject events, so this function
never touches DynamoDB directly.

Also enriches every event in that scoreboard payload with each
participating team's current head coach, injury report, and depth chart
(see enrichment.enrich_events) before writing it -- embedded directly
into the same scoreboard JSON, not a separate S3 object, both to keep
the write atomic (normalize's _process_scoreboard reads it all from one
place) and because scoreboard_event_to_event_item (library/normalize/
espn.py) rebuilds the whole event item from scratch every time it runs
-- so this enrichment has to happen on EVERY ingest run, not a lighter
subset of runs, or a run that omitted it would silently wipe out a
previous run's coach/injury/depth-chart fields on the next normalize
rebuild. This is also why ingest runs daily now instead of just
Tue/Wed: injury reports change through the week in a way box scores and
coach data don't.

Also fetches every participating team's current full roster (not just the
depth chart's skill-position subset -- see NFLClient.get_roster) and
writes it to its own S3 object per team, on every run, uncached -- unlike
coach/depth-chart data below, this one specifically exists to catch a
roster move (trade, signing, release) as soon as possible, so caching it
across days would defeat its own purpose. normalize picks each one up via
the same S3 PutObject trigger as everything else here and corrects that
team's players' entity records (library.normalize.espn.
roster_to_player_entities) -- see library/storage/pipeline_storage.py's
upsert_player_entity docstring for the guard that keeps this from ever
clobbering a newer fact with a stale one. Previously, a player's team_id
was only ever derived from their own most recent player_game_stats row,
which meant it stayed wrong for as long as a traded/signed player hadn't
yet played a game for their new team.

Coach and depth-chart data are cached in S3 with their own TTLs (see
enrichment.COACHES_CACHE_TTL_DAYS/DEPTH_CHART_CACHE_TTL_DAYS) -- "every
ingest run" above is about what gets WRITTEN each time (always the
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
endpoint resolves season/type/week entirely from a `dates=YYYYMMDD` param,
and "most recent Sunday" is the same value on both the Tuesday run and
the Wednesday retry within the same NFL week, unlike "today" -- which
would otherwise depend on exactly when ESPN's own scoreboard calendar
rolls over to the next week, an undocumented detail this function has no
business depending on.

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
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

import enrichment
from library.http.espn_core import EspnCoreApiClient
from library.http.nfl import NFLClient
from library.storage.depth_chart_cache import home_away_team_ids

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
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


def _fetch_rosters(events: list[dict], client: NFLClient) -> tuple[int, int]:
    """Fetches and writes every participating team's current roster --
    one S3 object per team (deduplicated, same set-of-team-ids pattern
    enrichment.enrich_events uses), always fresh, never TTL-cached (see
    this module's own docstring for why). Best-effort per team, same
    convention as enrich_events -- one team's fetch failing shouldn't
    lose the others', and never blocks the scoreboard/box-score writes
    that already ran before this."""
    team_ids: set[str] = set()
    for event in events:
        ids = home_away_team_ids(event)
        if ids is not None:
            team_ids.update(ids)

    fetched = failed = 0
    for team_id in team_ids:
        try:
            roster = client.get_roster(team_id)
            _put_json(f"nfl/roster/{team_id}.json", roster)
            fetched += 1
        except Exception:
            logger.exception("Failed fetching roster for team %s", team_id)
            failed += 1
    return fetched, failed


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
        # site.web.api.espn.com has no top-level "season" key (unlike the
        # old site.api.espn.com host this was written against) -- season
        # year/type live under leagues[0].season instead, and type is
        # itself a dict ({"id": "2", "type": 2, ...}) rather than a bare
        # int. week is unaffected -- still top-level.
        scoreboard = client.get_scoreboard_for_date(_most_recent_sunday())
        league_season = (scoreboard.get("leagues") or [{}])[0].get("season", {})
        season = league_season.get("year", season)
        season_type = league_season.get("type", {}).get("type", season_type)
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
    enrichment.enrich_events(events, season, client, core_client, _s3, RAW_BUCKET)

    scoreboard_key = f"nfl/scoreboard/{season}/{season_type}/{week}.json"
    _put_json(scoreboard_key, scoreboard)

    rosters_fetched, rosters_failed = _fetch_rosters(events, client)
    logger.info("Rosters: %d fetched, %d failed", rosters_fetched, rosters_failed)

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
    }
