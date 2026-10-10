"""
NHL schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
see Terraform/scheduler-nhl-schedule-sync.tf. In one invocation, walks up
to SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS calendar dates from today and writes
each date's scoreboard to S3 under the same nhl/scoreboard/{date}.json key
ingest uses, so the normalize Lambda's S3 trigger upserts the events into
DynamoDB -- including each game's probable starting goalies.

Exists because daily ingest only ever fetches yesterday's date: nothing
else seeds future games, which the upcoming-events list and the season
projection's remaining schedule both need.

Never fetches box scores. One shared NHLClient paces every request.

A date already written is skipped, except inside
SCHEDULE_SYNC_REFRESH_WINDOW_DAYS, where every date is re-fetched on every
run: a rescheduled game changes both its old and new date, and probable
goalies change day to day. A date with nothing to ingest (no games, or
preseason/exhibition only -- library.normalize.nhl.is_ingestable_event) is
never written, so it is re-checked each run.
"""
import json
import logging
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

from library.aws.account import get_account_id
from library.aws.boto_config import DEFAULT_CONFIG
from library.http.nhl import NHLClient
from library.normalize.nhl import is_ingestable_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nhl-schedule-sync")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

# Early October through the late-June Final, with padding.
SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS = 280
SCHEDULE_SYNC_REFRESH_WINDOW_DAYS = 14

_s3 = boto3.client("s3", config=DEFAULT_CONFIG)


def _object_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=RAW_BUCKET, Key=key, ExpectedBucketOwner=get_account_id())
        return True
    except ClientError:
        return False


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(
        Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json",
        ExpectedBucketOwner=get_account_id(),
    )


def lambda_handler(event: dict, context) -> dict:
    start = date.today()
    client = NHLClient()
    result = {"synced": 0, "refreshed": 0, "skipped": 0, "failed": 0}

    for offset in range(SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS):
        target_date = (start + timedelta(days=offset)).strftime("%Y%m%d")
        scoreboard_key = f"nhl/scoreboard/{target_date}.json"
        already_written = _object_exists(scoreboard_key)
        if already_written and offset >= SCHEDULE_SYNC_REFRESH_WINDOW_DAYS:
            result["skipped"] += 1
            continue

        try:
            scoreboard = client.get_scoreboard_for_date(target_date)
            if not any(is_ingestable_event(evt) for evt in scoreboard.get("events", [])):
                result["skipped"] += 1
                continue
            _put_json(scoreboard_key, scoreboard)
            result["refreshed" if already_written else "synced"] += 1
        # One date's failure doesn't block the rest; the next run retries it.
        except Exception:
            logger.exception("Failed syncing date %s", target_date)
            result["failed"] += 1

    logger.info(
        "Done: %d synced, %d refreshed, %d skipped, %d failed",
        result["synced"], result["refreshed"], result["skipped"], result["failed"],
    )
    return result
