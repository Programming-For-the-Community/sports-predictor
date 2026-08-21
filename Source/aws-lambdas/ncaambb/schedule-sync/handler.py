"""
NCAA MBB schedule-sync Lambda. Triggered directly by EventBridge Scheduler
-- see Terraform/scheduler-ncaambb-schedule-sync.tf. In ONE invocation,
walks up to SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS calendar dates from today and
writes each date's scoreboard to S3 under the exact same
ncaambb/scoreboard/{date}.json key ingest/handler.py already uses -- the
existing normalize Lambda's S3 trigger picks these up and upserts events
into DynamoDB automatically, same as daily ingest, no new normalize code
needed.

Exists because daily ingest (aws-lambdas/ncaambb/ingest/handler.py) only
ever fetches yesterday's date per run -- nothing seeds future dates ahead
of time, so the frontend's upcoming-events list would otherwise only ever
show whatever ingest happened to backfill after the fact, one day late.

Deliberately does not fetch box scores or rosters -- meaningless days/weeks
ahead of a game that hasn't been played. Daily ingest remains the only
source of box-score/roster fetches. Unlike ingest's own roster/box-score
loops, this Lambda's per-invocation volume (one scoreboard call per date,
not per game) doesn't scale with NCAA MBB's ~150-game-Saturday volume --
it's architecturally identical to NBA's own schedule-sync regardless of
how many games land on any one of the dates it walks, so it stays
sequential rather than needing the ThreadPoolExecutor ingest/handler.py
uses (see that module's own VOLUME docstring section).

ONE shared NCAAMBBClient for the whole run, not a separate client per
date -- every one of this run's own ESPN requests is paced by the SAME
RateLimiter instance, so nothing here can burst past ESPN's rate limit
regardless of how many dates are being synced.

There is no preseason concept to skip here -- confirmed live, 2026-08-19
(see project-ncaambb-onboarding memory and ingest/handler.py's own
docstring): ESPN's NCAA MBB scoreboard has zero events before the real
regular-season start date, and the earliest games of a season already
carry season.type=2/"regular-season".

Full-season walk with an idempotent skip-if-already-synced:
season_projection.py's own remaining_games input needs the whole rest of
the season seeded in DynamoDB, not just the next two weeks.
SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS is a generous fixed ceiling, not a real
season-end lookup -- walking past the actual National Championship just
costs a few empty-events calls.

The idempotent skip (_object_exists) is what keeps this affordable on a
daily schedule despite the large ceiling: after the season is fully
seeded, every run only ever calls ESPN for dates it hasn't already
written -- in steady state, just the one new day the rolling window added
since yesterday.

RESCHEDULE HANDLING: the idempotent skip above only applies beyond
SCHEDULE_SYNC_REFRESH_WINDOW_DAYS -- every date inside that near-term
window is re-fetched and overwritten on every run, regardless of whether
it was already written. A rescheduled game moves both its old date's
entry (now stale, still lists a game that no longer plays there) and its
new date's entry (missing, if that date was already synced before the
move happened) -- neither self-corrects under a pure idempotent skip;
ESPN's own scoreboard response is the only source of truth for "what's
actually on this date now" and has to be re-asked. Scoped to the
near-term window, not every date: re-fetching all ~270 dates daily would
cost ~270 ESPN calls/run for a class of change (reschedule) that's rare
and virtually never announced more than a few weeks out -- a date
already outside the window when this runs will have entered it (and been
caught) well before it's played. Beyond the window, a date still
ultimately self-corrects once the game is actually played: daily
ingest's own "yesterday" fetch always writes that day's real
scoreboard/box score unconditionally, regardless of what schedule-sync
already wrote for it -- the refresh window just makes that correction
happen days/weeks earlier, before the game, not only after.
"""
import json
import logging
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

from library.http.ncaambb import NCAAMBBClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("ncaambb-schedule-sync")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

# Generous fixed ceiling covering the whole regular season plus
# conference/NCAA tournaments with real padding on both ends -- not a real
# season-end lookup. Combined with the idempotent skip below, walking a
# bit past the actual end of the season just costs a few cheap
# empty-events calls. Same value as NBA's own ceiling -- both are just
# "generous," not tied precisely to either sport's actual season length.
SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS = 270

# Every date inside this window is re-fetched every run regardless of the
# idempotent skip below -- see this module's own RESCHEDULE HANDLING
# docstring section. 14 days is a deliberate balance: cheap (14 extra
# ESPN calls/run) while still catching a reschedule with real advance
# notice before the game, not just after it's already happened.
SCHEDULE_SYNC_REFRESH_WINDOW_DAYS = 14

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
    client = NCAAMBBClient()

    synced = refreshed = skipped = failed = 0
    for offset in range(SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS):
        target_date = (start + timedelta(days=offset)).strftime("%Y%m%d")
        scoreboard_key = f"ncaambb/scoreboard/{target_date}.json"

        already_written = _object_exists(scoreboard_key)
        in_refresh_window = offset < SCHEDULE_SYNC_REFRESH_WINDOW_DAYS
        if already_written and not in_refresh_window:
            skipped += 1
            continue

        try:
            scoreboard = client.get_scoreboard_for_date(target_date)
            _put_json(scoreboard_key, scoreboard)
            # A refresh-window overwrite still re-triggers normalize's own
            # S3 PUT trigger the exact same way a first-time write does --
            # this only distinguishes the two for observability (how many
            # dates actually had a schedule change worth re-fetching vs.
            # how many were newly seeded), not for any behavior difference.
            if already_written:
                refreshed += 1
            else:
                synced += 1
        except Exception:
            # One date's transient ESPN failure doesn't block the rest of
            # the lookahead window from syncing -- tomorrow's scheduled
            # run retries it anyway (either its S3 object was never
            # written, so the idempotent skip won't hide the retry, or
            # it's still inside the refresh window and gets retried
            # unconditionally regardless).
            logger.exception("Failed syncing date %s", target_date)
            failed += 1

    logger.info(
        "Schedule sync complete: %d synced, %d refreshed, %d skipped, %d failed", synced, refreshed, skipped, failed,
    )
    return {"synced": synced, "refreshed": refreshed, "skipped": skipped, "failed": failed}
