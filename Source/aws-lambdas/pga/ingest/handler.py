"""
PGA ingest Lambda. Triggered daily by the shared ingest-orchestrator Step
Function (Terraform/sfn-ingest-orchestrator.tf), which invokes every
active sport's own "${project}-<sport>-ingest" Lambda by naming
convention -- see project-pga-onboarding memory: neither orchestrator
branches on event_type, so PGA needs no orchestration change to onboard
through this same path.

Genuinely different shape from every head-to-head sport's ingest: there is
no per-day slate of games and no separate box-score fetch. A PGA
tournament is one event id that persists across its whole Thu-Sun window,
and the richer leaderboard endpoint (PGAClient.get_leaderboard) already
carries full per-competitor results -- there's nothing else to fetch once
an event id is known. This Lambda's only job is: ask the scoreboard for
today's date which event id(s) are current, then write each one's raw
leaderboard JSON to S3; the normalize Lambda (triggered by that S3
PutObject) does the rest.

Defaults to today, not yesterday (unlike NBA's ingest) -- a PGA
tournament's own status (in-progress vs. final) is what determines
whether its result is usable, not which calendar day it is, and writing
today's snapshot lets an in-progress tournament's leaderboard refresh
daily through the week rather than only once after it ends. Confirmed
live 2026-08-24: querying a date the day after a tournament's endDate
still returns that tournament, so this is not delicately timed.

Discovering NEW tournaments before their own start date (so the frontend
has something to show ahead of time) is schedule-sync's job, not this
Lambda's -- see aws-lambdas/pga/schedule-sync/handler.py.

Also captures a daily season-stats snapshot (PGAClient.get_statistics --
driving distance/accuracy, GIR%, putts per hole, scoring average, etc.),
unconditionally, every run, regardless of whether a tournament is
current -- same "run it every day no matter what" treatment NBA's own
daily roster/injury refresh gets, and for the same underlying reason:
this is the ONLY way this project can ever have a historical value for
these categories at all. Confirmed live, 2026-08-25, that ESPN's own
statistics endpoint is CURRENT-SNAPSHOT-ONLY (its season/year query
params are silently ignored -- see PGAClient.get_statistics' own
docstring), so there is no backfill path for this data; every day this
Lambda doesn't capture a snapshot is a day of history this project can
never recover. Written to its own raw prefix (pga/statistics/{date}.json),
never routed through normalize/DynamoDB -- feature-engineering reads
these raw snapshots directly from S3, same pattern NCAA MBB's AP-poll
snapshots already use (design/DATA_SCHEMA.md).
"""
import json
import logging
import os
from datetime import date

import boto3

from library.http.pga import PGAClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("pga-ingest")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

_s3 = boto3.client("s3")


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")
    logger.info("Wrote s3://%s/%s", RAW_BUCKET, key)


def _fetch_statistics_snapshot(client: PGAClient, target_date: str) -> bool:
    """Best-effort -- a failed stats fetch shouldn't block leaderboard
    ingest, the actual primary job of this Lambda. Returns whether it
    succeeded, for the caller's own result summary."""
    try:
        statistics = client.get_statistics()
        _put_json(f"pga/statistics/{target_date}.json", statistics)
        return True
    except Exception:
        logger.exception("Failed fetching season-stats snapshot for date %s", target_date)
        return False


def lambda_handler(event: dict, context) -> dict:
    target_date = event.get("date") or date.today().strftime("%Y%m%d")
    client = PGAClient()

    stats_captured = _fetch_statistics_snapshot(client, target_date)

    scoreboard = client.get_scoreboard_for_date(target_date)
    events = scoreboard.get("events", [])
    logger.info("Found %d current event(s) for date %s", len(events), target_date)

    processed = failed = 0
    for evt in events:
        event_id = evt["id"]
        season = evt.get("season", {}).get("year")
        try:
            leaderboard = client.get_leaderboard(event_id)
            _put_json(f"pga/leaderboard/{season}/{event_id}.json", leaderboard)
            processed += 1
        except Exception:
            logger.exception("Failed fetching leaderboard for event %s", event_id)
            failed += 1

    logger.info("Done: %d processed, %d failed, stats_captured=%s", processed, failed, stats_captured)
    return {"processed": processed, "failed": failed, "stats_captured": stats_captured}
