"""
NFL schedule-sync Lambda. In one invocation, walks every week of the
current NFL season (regular season 1-18, postseason 1-5) and writes each
week's scoreboard to S3 under the same
nfl/scoreboard/{season}/{type}/{week}.json key ingest/handler.py uses --
the existing normalize Lambda's S3 trigger picks these up and upserts
events into DynamoDB automatically.

Does not call ingest/handler.py's enrichment for coach/injury data, and
does not fetch box scores -- both are meaningless months ahead of a game
that hasn't been played. Does attach depth charts
(library.storage.depth_chart_cache).

Uses one shared NFLClient for the whole run so every request is paced
by the same RateLimiter instance.
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

# Looping through week 18 is harmless for pre-2021 seasons (only 17 weeks
# existed then) -- ESPN just returns an empty events list for that week.
# Preseason (season_type 1) is deliberately absent.
REGULAR_SEASON_WEEKS = range(1, 19)
POSTSEASON_WEEKS = range(1, 6)
SEASON_TYPES = {"regular": 2, "postseason": 3}

_s3 = boto3.client("s3")


def _current_nfl_season(today: date | None = None) -> int:
    """The NFL season year for `today`: Sep-Dec resolves to this year (the
    season in progress), Jan-Feb resolves to last year (still finishing
    that season's playoffs -- the Super Bowl is in February), and Mar-Aug
    resolves to this year (the upcoming, not-yet-started season)."""
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
                logger.exception("Failed syncing season %s type %d week %d", season, season_type, week)
                failed += 1

    logger.info("Schedule sync for season %s complete: %d synced, %d failed", season, synced, failed)
    return {"season": season, "synced": synced, "failed": failed}
