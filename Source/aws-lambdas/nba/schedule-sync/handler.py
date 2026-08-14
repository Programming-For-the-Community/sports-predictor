"""
NBA schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
see Terraform/scheduler-nba-schedule-sync.tf. In ONE invocation, walks
the next SCHEDULE_SYNC_LOOKAHEAD_DAYS calendar dates from today and writes
each date's scoreboard to S3 under the exact same nba/scoreboard/{date}.json
key ingest/handler.py already uses -- the existing normalize Lambda's S3
trigger picks these up and upserts events into DynamoDB automatically,
same as daily ingest, no new normalize code needed.

Exists because daily ingest (aws-lambdas/nba/ingest/handler.py) only ever
fetches YESTERDAY's date per run -- nothing seeds future dates ahead of
time, so the frontend's upcoming-events list would otherwise only ever
show whatever ingest happened to backfill after the fact, one day late.

Deliberately does NOT fetch box scores -- meaningless days/weeks ahead of
a game that hasn't been played. Daily ingest remains the ONLY source of
box-score fetches. No depth-chart/coach/roster refresh here either, same
division of responsibility as NFL's own schedule-sync (those stay on
ingest's daily cadence).

ONE shared NBAClient for the whole run, not a separate client per date --
every one of this run's ~LOOKAHEAD_DAYS requests is paced by the SAME
RateLimiter instance, so nothing here can burst past ESPN's rate limit
regardless of how many dates are being synced.

Preseason dates are skipped, same PRESEASON_TYPE convention and same
"don't seed data that would skew training" reasoning as ingest/handler.py's
own docstring.

KNOWN GAP: unlike NFL's/NCAAFB's own schedule-sync, this does NOT seed
the whole season -- only a 14-day lookahead (see Terraform/
lambda-nba-schedule-sync.tf's own comment for why: a full ~170-day daily
walk would be 170+ ESPN calls every scheduled run, against this phase's
own API-minimization principle). Season simulation (Sub-phase 3A step 8)
will very likely need the full remaining season seeded in DynamoDB the
way NFL's schedule-sync provides for _season_standings_inputs'
remaining_games -- revisit this Lambda then, not before.
"""
import json
import logging
import os
from datetime import date, timedelta

import boto3

from library.http.nba import NBAClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nba-schedule-sync")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]
PRESEASON_TYPE = 1

# Two weeks -- long enough that a frontend upcoming-events window (however
# that ends up scoped in Sub-phase 3A step 6's library/serving/nba_reads.py)
# always has real data ahead of it, short enough that a single run stays
# well within ESPN's practical rate limits (one call per date, paced by
# the same RateLimiter every other ESPN call in this project uses).
SCHEDULE_SYNC_LOOKAHEAD_DAYS = 14

_s3 = boto3.client("s3")


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")


def lambda_handler(event: dict, context) -> dict:
    start = date.today()
    client = NBAClient()

    synced = skipped = failed = 0
    for offset in range(SCHEDULE_SYNC_LOOKAHEAD_DAYS):
        target_date = (start + timedelta(days=offset)).strftime("%Y%m%d")
        try:
            scoreboard = client.get_scoreboard_for_date(target_date)
            events = scoreboard.get("events", [])
            if events and events[0].get("season", {}).get("type") == PRESEASON_TYPE:
                logger.debug("Date %s is preseason -- skipping, not synced by design", target_date)
                skipped += 1
                continue
            _put_json(f"nba/scoreboard/{target_date}.json", scoreboard)
            synced += 1
        except Exception:
            # One date's transient ESPN failure doesn't block the rest of
            # the lookahead window from syncing -- tomorrow's scheduled
            # run retries it anyway.
            logger.exception("Failed syncing date %s", target_date)
            failed += 1

    logger.info("Schedule sync complete: %d synced, %d skipped, %d failed", synced, skipped, failed)
    return {"synced": synced, "skipped": skipped, "failed": failed}
