"""
NFL schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
see Terraform/scheduler-nfl-schedule-sync.tf. In ONE invocation, walks
every week of the current NFL season (regular season 1-18, postseason
1-5) and writes each week's scoreboard to S3 under the exact same
nfl/scoreboard/{season}/{type}/{week}.json key ingest/handler.py already
uses -- the existing normalize Lambda's S3 trigger picks these up and
upserts events into DynamoDB automatically, same as daily ingest, no new
normalize code needed.

Exists because daily ingest (aws-lambdas/nfl/ingest/handler.py) only ever
fetches ONE week per run (whichever week "most recent Sunday" auto-
detects) -- nothing seeded future weeks ahead of time, so
Source/aws-lambdas/nfl/predict/handler.py's season simulation
(_season_standings_inputs' remaining_games) and the frontend's upcoming-
events list (library.serving.nfl_reads._next_week_events) had nothing to
look ahead into.

Deliberately does NOT call ingest/handler.py or its _enrich_events for
coach/injury data, and does not fetch box scores -- both are meaningless
months ahead of a game that hasn't been played. Daily ingest remains the
ONLY source of coach/injury freshness and of box-score fetches.

Does attach depth charts (library.storage.depth_chart_cache) -- position
assignments don't need daily refreshing the way injuries do.

ONE shared NFLClient for the whole run, not a separate client per week --
every one of this run's ~23 requests is paced by the SAME RateLimiter
instance, the same guarantee data-backfills/nfl/backfill.py's own
season-wide sweep relies on, so nothing here can burst past ESPN's rate
limit regardless of how many weeks are being synced.
"""
import json
import logging
import os
from datetime import date

import boto3

from library.http.nfl import NFLClient
from library.storage.depth_chart_cache import attach_depth_charts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nfl-schedule-sync")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

# Mirrors data-backfills/nfl/backfill.py's REGULAR_SEASON_WEEKS/
# POSTSEASON_WEEKS/SEASON_TYPES -- same NFL structural fact, duplicated
# rather than imported since this is a separate Lambda deployment package
# (same reasoning PLAYER_PROP_STATS's own duplication comment in
# predict/handler.py documents). Looping through week 18 is harmless for
# pre-2021 seasons (only 17 weeks existed then) -- ESPN just returns an
# empty events list for that week. Preseason (season_type 1) deliberately
# absent, matching ingest/handler.py's own PRESEASON_TYPE skip.
REGULAR_SEASON_WEEKS = range(1, 19)
POSTSEASON_WEEKS = range(1, 6)
SEASON_TYPES = {"regular": 2, "postseason": 3}

_s3 = boto3.client("s3")


def _current_nfl_season(today: date | None = None) -> int:
    """The NFL season year for `today`: Sep-Dec resolves to this year (the
    season in progress), Jan-Feb resolves to last year (still finishing
    that season's playoffs -- the Super Bowl is in February), and Mar-Aug
    resolves to this year (the upcoming, not-yet-started season -- this
    is what lets an off-season run seed next season's schedule before
    Week 1 has even been played). Only used when the schedule's input
    doesn't override `season` explicitly."""
    today = today or date.today()
    return today.year if today.month >= 3 else today.year - 1


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(
        Bucket=RAW_BUCKET,
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )


def lambda_handler(event: dict, context) -> dict:
    season = event.get("season") or _current_nfl_season()
    client = NFLClient()

    synced = failed = 0
    for season_type, weeks in ((SEASON_TYPES["regular"], REGULAR_SEASON_WEEKS), (SEASON_TYPES["postseason"], POSTSEASON_WEEKS)):
        for week in weeks:
            try:
                scoreboard = client.get_scoreboard(season, season_type, week)
                attach_depth_charts(scoreboard.get("events", []), client, _s3, RAW_BUCKET)
                _put_json(f"nfl/scoreboard/{season}/{season_type}/{week}.json", scoreboard)
                synced += 1
            except Exception:
                # One week's transient ESPN failure doesn't block the rest
                # of the season's weeks from syncing -- next week's
                # scheduled run retries it anyway.
                logger.exception("Failed syncing season %d type %d week %d", season, season_type, week)
                failed += 1

    logger.info("Schedule sync for season %d complete: %d synced, %d failed", season, synced, failed)
    return {"season": season, "synced": synced, "failed": failed}
