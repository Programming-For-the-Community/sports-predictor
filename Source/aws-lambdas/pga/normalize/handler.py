"""
PGA normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the pga/ prefix (see
Terraform/s3-raw-data-lake-notifications.tf). Reads raw ESPN leaderboard
JSON written by pga-ingest/pga-schedule-sync from S3, normalizes it into
the project schema, and upserts the results into DynamoDB. Never calls
ESPN directly.

Only one raw payload shape exists for PGA, unlike every head-to-head
sport's normalize Lambda (which routes teams.json/scoreboard/boxscore/
roster keys separately): pga/leaderboard/{season}/{event_id}.json is the
ONLY key pattern either ingest or schedule-sync ever writes -- one
leaderboard fetch already carries both the tournament (event) and every
competitor's own entity data, since there's no separate roster source for
golf (see library/normalize/pga.py's own docstring) and no player_game_
stats table for a field-event sport (design/DATA_SCHEMA.md) -- so there's
nothing else to route.

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.
"""
import json
import logging
import urllib.parse

import boto3

from library.normalize.pga import is_medal_scoring, leaderboard_event_to_event_item, leaderboard_event_to_player_entities
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("pga-normalize")

SPORT = "pga"

_s3 = boto3.client("s3")
_storage: PipelineStorage | None = None


def _get_storage() -> PipelineStorage:
    # Initialized once per container lifetime, reused across warm invocations.
    global _storage
    if _storage is None:
        _storage = PipelineStorage()
    return _storage


def _process_leaderboard(payload: dict, key: str) -> None:
    storage = _get_storage()
    events = payload.get("events", [])
    if not events:
        logger.warning("No events in leaderboard payload %s", key)
        return
    event = events[0]

    # Ryder Cup/Presidents Cup/WGC Match Play (team or individual match
    # play) and Zurich Classic of New Orleans (team stroke play) are real
    # PGA TOUR calendar entries this project's schema and normalizers
    # don't support -- see is_medal_scoring's own docstring for the
    # confirmed-live crash this guards against. Raw JSON for these is
    # still written to S3 by ingest/schedule-sync (harmless, preserves
    # the record), it just never reaches DynamoDB.
    if not is_medal_scoring(event):
        logger.info(
            "Skipping %s -- not stroke-play scoring (tournament=%r, scoringSystem=%r)",
            key, (event.get("tournament") or {}).get("displayName"),
            (event.get("tournament") or {}).get("scoringSystem", {}).get("name"),
        )
        return

    # A real, confirmed ESPN gap distinct from the above -- a Medal-
    # scoring event whose own competition object has no "competitors" key
    # at all (confirmed live, 2026-08-26, on a small cluster of 2020
    # COVID-canceled tournaments plus a few real completed ones ESPN never
    # populated -- see data-backfills/pga/backfill.py's matching check and
    # design/DATA_SCHEMA.md for the full writeup). Writing this event
    # anyway would corrupt the cutline dataset's field_size feature
    # (len(participants)) to 0 for a real full field, so it's skipped
    # entirely -- same treatment as the no-events-in-payload case above.
    competition = event["competitions"][0] if event.get("competitions") else {}
    if not competition.get("competitors"):
        logger.warning(
            "Event %s in %s is Medal scoring but has no competitor data (status=%s) -- a real ESPN gap, not written.",
            event.get("id"), key, (event.get("status") or {}).get("type", {}).get("name"),
        )
        return

    # Entities upserted before the event that references them, same
    # ordering every head-to-head normalizer uses -- not load-bearing
    # (DynamoDB has no foreign-key enforcement), but keeps a reader that
    # queries right after this run from ever seeing an event whose
    # participants' entities don't exist yet. Plain upsert_entity, not
    # upsert_player_entity -- that guard is specifically for team_id
    # staleness, a concept golfers (no team) don't have.
    entities = leaderboard_event_to_player_entities(event, SPORT)
    for entity in entities:
        storage.upsert_entity(entity)

    storage.upsert_event(leaderboard_event_to_event_item(event, SPORT))
    logger.info("Upserted 1 event and %d player entities from %s", len(entities), key)


def _dispatch(bucket: str, key: str) -> None:
    response = _s3.get_object(Bucket=bucket, Key=key)
    payload = json.loads(response["Body"].read())

    if "/leaderboard/" in key:
        _process_leaderboard(payload, key)
    else:
        logger.warning("Unrecognized S3 key pattern, skipping: %s", key)


def lambda_handler(event: dict, context) -> dict:
    records = event.get("Records", [])
    processed = failed = 0

    for record in records:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        try:
            _dispatch(bucket, key)
            processed += 1
        except Exception:
            logger.exception("Failed processing s3://%s/%s", bucket, key)
            failed += 1

    return {"processed": processed, "failed": failed}
