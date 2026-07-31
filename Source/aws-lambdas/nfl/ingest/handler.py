"""
NFL ingest Lambda. Triggered by EventBridge Scheduler (weekly -- see
Terraform/lambda-nfl-ingest.tf). Fetches the current week's scoreboard
and completed box scores from ESPN and writes raw JSON to S3. The
normalize Lambda is triggered automatically by the resulting S3 PutObject
events, so this function never touches DynamoDB directly.

EventBridge can override any default via the schedule's input payload:
    { "season": 2025, "season_type": 2, "week": 4 }

All three are optional. Omitting "week" tells ESPN to return the current
week for the given season and type. Omitting everything lets ESPN infer
today's season/type/week on its own -- the scoreboard endpoint defaults to
"today" when called with no query params at all, so the schedule doesn't
need to carry a season/season_type payload and nothing here needs updating
across season or season-type transitions.

Preseason (season_type 1) is never ingested, whether auto-detected or
passed explicitly -- backup-heavy preseason rosters and results aren't
representative of regular-season performance and would skew training data.
This matches the historical backfill, which only ever pulls regular season
and postseason (see SEASON_TYPES in data-backfills/nfl/backfill.py).
"""
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from library.http.nfl import NFLClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-ingest")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]
PRESEASON_TYPE = 1

_s3 = boto3.client("s3")


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


def lambda_handler(event: dict, context) -> dict:
    season = event.get("season")
    season_type = event.get("season_type")
    week = event.get("week")

    if season_type == PRESEASON_TYPE:
        logger.info("season_type=%d is preseason -- skipping, not ingested by design", PRESEASON_TYPE)
        return {"processed": 0, "skipped": 0, "failed": 0}

    client = NFLClient()

    if week is None:
        # Omitted season/season_type are passed through as None, which
        # NFLClient drops from the request entirely -- ESPN then infers
        # season/type/week from today's date server-side.
        scoreboard = client.get_current_scoreboard(season, season_type)
        season = scoreboard.get("season", {}).get("year", season)
        season_type = scoreboard.get("season", {}).get("type", season_type)
        week = scoreboard.get("week", {}).get("number", 1)

        if season_type == PRESEASON_TYPE:
            logger.info("Auto-detected preseason (season %d) -- skipping, not ingested by design", season)
            return {"processed": 0, "skipped": 0, "failed": 0}

        logger.info("Auto-detected season %d type %d week %d", season, season_type, week)
    else:
        scoreboard = client.get_scoreboard(season, season_type, week)

    scoreboard_key = f"nfl/scoreboard/{season}/{season_type}/{week}.json"
    _put_json(scoreboard_key, scoreboard)

    events = scoreboard.get("events", [])
    logger.info("Found %d events in season %d type %d week %d", len(events), season, season_type, week)

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
    return {"processed": processed, "skipped": skipped, "failed": failed}