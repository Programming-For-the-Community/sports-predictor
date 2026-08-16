"""
NBA schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
see Terraform/scheduler-nba-schedule-sync.tf. In ONE invocation, walks up
to SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS calendar dates from today and writes
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
every one of this run's own ESPN requests is paced by the SAME
RateLimiter instance, so nothing here can burst past ESPN's rate limit
regardless of how many dates are being synced.

Preseason dates are skipped, same PRESEASON_TYPE convention and same
"don't seed data that would skew training" reasoning as ingest/handler.py's
own docstring.

FULL-SEASON WALK, idempotent skip-if-already-synced (2026-08-16, replacing
the earlier 14-day-only version -- see project-nba-onboarding memory for
why that was deliberately scoped down at step 3, and why Sub-phase 3A step
8 (season simulation) is what finally needed the fix: season_projection.py's
own remaining_games input, same role NFL's/NCAAFB's own full-season
schedule-sync already provides, needs the WHOLE rest of the season seeded
in DynamoDB, not just the next two weeks). SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS
is a generous fixed ceiling (same "harmless if empty" philosophy as
data-backfills/nba/backfill.py's own season_date_range), not a real
season-end lookup -- walking past the actual Finals just costs a few
empty-events calls, no worse than backfill's own padding.

The idempotent skip (_object_exists, same helper/reasoning as ingest's own
box-score skip) is what keeps this affordable on a DAILY schedule despite
the large ceiling: the first run after this shipped pays the full one-time
cost of seeding the whole season (a few hundred ESPN calls, paced by the
shared RateLimiter -- see this Lambda's own timeout, raised accordingly),
but every run after that only ever calls ESPN for dates it hasn't already
written -- in steady state, just the one new day the rolling window added
since yesterday, plus however many still-preseason dates remain unwritten
(preseason dates are deliberately never written -- see the loop below --
so they get re-checked, and re-cost one call, on every run until the
season actually starts).

KNOWN LIMITATION: a date, once written, is never re-fetched -- if the NBA
reschedules a future game to a different date after this Lambda has
already synced its original date, that date's now-stale entry won't
self-correct here. It corrects itself once the game is actually played:
daily ingest's own "yesterday" fetch always writes that day's real
scoreboard/box score unconditionally, regardless of what schedule-sync
already wrote for it. Until then, the frontend may show the old date --
a cosmetic gap, not a data-correctness one, and rare enough (NBA
reschedules are uncommon) not to justify a TTL/re-check mechanism.
"""
import json
import logging
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

from library.http.nba import NBAClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nba-schedule-sync")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]
PRESEASON_TYPE = 1

# Generous fixed ceiling covering preseason through the NBA Finals with
# real padding on both ends (same philosophy as backfill's own
# season_date_range) -- not a real season-end lookup. Combined with the
# idempotent skip below, walking a bit past the actual end of the season
# just costs a few cheap empty-events calls, not a real problem.
SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS = 270

_s3 = boto3.client("s3")


def _object_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=RAW_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")


def lambda_handler(event: dict, context) -> dict:
    start = date.today()
    client = NBAClient()

    synced = skipped = failed = 0
    for offset in range(SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS):
        target_date = (start + timedelta(days=offset)).strftime("%Y%m%d")
        scoreboard_key = f"nba/scoreboard/{target_date}.json"

        if _object_exists(scoreboard_key):
            skipped += 1
            continue

        try:
            scoreboard = client.get_scoreboard_for_date(target_date)
            events = scoreboard.get("events", [])
            if events and events[0].get("season", {}).get("type") == PRESEASON_TYPE:
                logger.debug("Date %s is preseason -- skipping, not synced by design", target_date)
                skipped += 1
                continue
            _put_json(scoreboard_key, scoreboard)
            synced += 1
        except Exception:
            # One date's transient ESPN failure doesn't block the rest of
            # the lookahead window from syncing -- tomorrow's scheduled
            # run retries it anyway (its own S3 object was never written,
            # so the idempotent skip above won't hide the retry).
            logger.exception("Failed syncing date %s", target_date)
            failed += 1

    logger.info("Schedule sync complete: %d synced, %d skipped, %d failed", synced, skipped, failed)
    return {"synced": synced, "skipped": skipped, "failed": failed}
