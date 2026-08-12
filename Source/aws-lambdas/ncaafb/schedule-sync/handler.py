"""
NCAAFB schedule-sync Lambda. Triggered directly by EventBridge Scheduler
-- see Terraform/scheduler-ncaafb-schedule-sync.tf. In ONE invocation,
walks every week of the current season and writes each week's games to
S3 under the exact same ncaafb/games/{season}/{season_type}/{week}.json
key ingest/handler.py already uses -- normalize's S3 trigger
(Terraform/s3-raw-data-lake-notifications.tf) picks these up the same
way daily ingest's output does, no new normalize code needed.

Exists for the same reason as NFL's own schedule-sync: daily ingest only
ever fetches the current week, so nothing seeds future weeks ahead of
time for season simulation or the frontend's upcoming-events list.

Deliberately does NOT call CFBD's /coaches or /rankings endpoints (see
aws-lambdas/ncaafb/ingest/enrichment.py's own docstring on why full
enrichment is ingest-only) -- rankings especially would be meaningless
weeks ahead of a game, and re-fetching them once per week of a
season-wide walk would blow well past the project plan's 1-call/week
ranking budget. Does attach venue_indoor
(library.storage.ncaafb_team_cache.attach_venue_indoor) -- cheap (one
TTL-cached bulk call) and stable ahead of time, unlike rankings. Imports
that piece from the shared library package rather than ingest's own
enrichment.py -- Lambda deployment packages are built per-function (see
python_lambda_build.yml), so this Lambda's ZIP never contains ingest's
local files, only library/.

ONE shared CFBDClient for the whole run, not a separate client per week --
every one of this run's requests is paced by the SAME RateLimiter
instance, same reasoning as NFL's own schedule-sync.
"""
import json
import logging
import os
from datetime import date

import boto3

from library.http.cfbd import CFBDClient
from library.storage.ncaafb_team_cache import attach_venue_indoor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("ncaafb-schedule-sync")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

# Starts at 1, not 0 -- confirmed live (see data-backfills/ncaafb/
# backfill.py's own REGULAR_SEASON_WEEKS comment for the full CFBD
# quirk): GET /games?week=0 doesn't filter at all, it silently returns
# the ENTIRE season (868 games for one test call), which would get
# mislabeled and written to S3 as this season's "week 0" file and
# double-upsert every game via normalize's S3 trigger. Any real "Week 0"
# season-opener games are a known, accepted gap.
#
# A week past the real season length is still harmless (empty games
# list), same "generous ceiling" convention as NFL's own
# REGULAR_SEASON_WEEKS/POSTSEASON_WEEKS. Postseason (bowls plus the
# 12-team CFP through the championship) spans a handful of weeks in
# CFBD's own numbering.
REGULAR_SEASON_WEEKS = range(1, 17)
POSTSEASON_WEEKS = range(1, 6)
SEASON_TYPES = ("regular", "postseason")

_s3 = boto3.client("s3")


def _current_ncaafb_season(today: date | None = None) -> int:
    """Mirrors ingest/handler.py's own _current_ncaafb_season -- same
    duplication convention NFL's schedule-sync/ingest pair already uses
    (each Lambda is its own deployment package)."""
    today = today or date.today()
    return today.year - 1 if today.month == 1 else today.year


def _put_json(key: str, payload) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")


def lambda_handler(event: dict, context) -> dict:
    season = event.get("season") or _current_ncaafb_season()
    client = CFBDClient()

    synced = failed = 0
    for season_type in SEASON_TYPES:
        weeks = REGULAR_SEASON_WEEKS if season_type == "regular" else POSTSEASON_WEEKS
        for week in weeks:
            try:
                games = client.get_games(season, week=week, season_type=season_type)
                attach_venue_indoor(games, season, client, _s3, RAW_BUCKET)
                _put_json(f"ncaafb/games/{season}/{season_type}/{week}.json", games)
                synced += 1
            except Exception:
                # One week's transient CFBD failure doesn't block the
                # rest of the season's weeks from syncing -- next week's
                # scheduled run retries it anyway.
                logger.exception("Failed syncing season %s type %s week %d", season, season_type, week)
                failed += 1

    logger.info("Schedule sync for season %s complete: %d synced, %d failed", season, synced, failed)
    return {"season": season, "synced": synced, "failed": failed}
