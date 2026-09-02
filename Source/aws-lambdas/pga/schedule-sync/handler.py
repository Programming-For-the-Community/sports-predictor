"""
PGA schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
see Terraform/scheduler-pga-schedule-sync.tf (Phase 5 step 4). In ONE
invocation, discovers every tournament on the current season's calendar
and writes each one's raw leaderboard JSON to S3 under the exact same
pga/leaderboard/{season}/{event_id}.json key ingest/handler.py already
uses -- the existing normalize Lambda's S3 trigger picks these up and
upserts events into DynamoDB automatically, same as daily ingest, no new
normalize code needed.

Genuinely cheaper than every head-to-head sport's own schedule-sync: NBA/
NFL/NCAAFB/NCAAMBB all walk ~200-270 individual calendar dates because
their schedules aren't otherwise discoverable in one call. PGA's
scoreboard endpoint returns its ENTIRE season's tournament list --
`response["leagues"][0]["calendar"]`, ~45-51 entries -- from a single
call regardless of which date is queried (even a date in the middle of
an off week, with zero "current" events, still returns the full season
calendar). So this Lambda makes one scoreboard
call to discover event ids, then up to one leaderboard call per
tournament -- never a per-date walk.

Exists because daily ingest (aws-lambdas/pga/ingest/handler.py) only asks
"what's the currently active tournament for today's date" -- it never
seeds a tournament that hasn't started yet, so the frontend's upcoming-
events list would otherwise stay empty until the week a tournament
actually begins. Confirmed live that a not-yet-started tournament's own
leaderboard fetch works fine (STATUS_SCHEDULED, every competitor pre-
listed with no score yet) -- see library/normalize/pga.py.

Idempotent skip, same shape as NBA's: a tournament already written to S3
is skipped UNLESS its startDate falls inside
SCHEDULE_SYNC_REFRESH_WINDOW_DAYS, in which case it's re-fetched and
overwritten every run -- catches an in-progress or just-finished
tournament's result updating through the week, plus any late field/course
change for a tournament about to start. Daily ingest's own "today" fetch
already refreshes whichever ONE tournament is active on any given day
(see its own docstring), so this window only needs to cover a small
buffer around that, not the whole season.

ONE shared PGAClient for the whole run -- same single-rate-limiter
reasoning as NBA's schedule-sync.
"""
import json
import logging
import os
from datetime import date, datetime

import boto3
from botocore.exceptions import ClientError

from library.http.pga import PGAClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("pga-schedule-sync")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

# Any tournament starting within this many days of "now" (in either
# direction -- a startDate in the past is still inside the window until
# well after the tournament itself has concluded) is re-fetched every run
# regardless of the idempotent skip below.
SCHEDULE_SYNC_REFRESH_WINDOW_DAYS = 10

_s3 = boto3.client("s3")


def _object_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=RAW_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")


def _in_refresh_window(start_date: str, today: date) -> bool:
    """start_date is ESPN's ISO 8601 calendar entry (e.g.
    "2026-08-20T04:00Z"). Inside the window if today falls within
    SCHEDULE_SYNC_REFRESH_WINDOW_DAYS days of it, on either side."""
    try:
        event_date = datetime.fromisoformat(start_date.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return True  # unparseable date -- err on the side of refreshing
    return abs((event_date - today).days) <= SCHEDULE_SYNC_REFRESH_WINDOW_DAYS


def lambda_handler(event: dict, context) -> dict:
    today = date.today()
    client = PGAClient()

    scoreboard = client.get_scoreboard_for_date(today.strftime("%Y%m%d"))
    leagues = scoreboard.get("leagues") or [{}]
    calendar = leagues[0].get("calendar") or []
    season = leagues[0].get("season", {}).get("year")
    logger.info("Season %s calendar has %d tournament(s)", season, len(calendar))

    synced = refreshed = skipped = failed = 0
    for entry in calendar:
        event_id = entry["id"]
        leaderboard_key = f"pga/leaderboard/{season}/{event_id}.json"

        already_written = _object_exists(leaderboard_key)
        if already_written and not _in_refresh_window(entry["startDate"], today):
            skipped += 1
            continue

        try:
            leaderboard = client.get_leaderboard(event_id)
            _put_json(leaderboard_key, leaderboard)
            if already_written:
                refreshed += 1
            else:
                synced += 1
        except Exception:
            logger.exception("Failed syncing tournament %s (%s)", event_id, entry.get("label"))
            failed += 1

    logger.info(
        "Schedule sync complete: %d synced, %d refreshed, %d skipped, %d failed", synced, refreshed, skipped, failed,
    )
    return {"synced": synced, "refreshed": refreshed, "skipped": skipped, "failed": failed}
