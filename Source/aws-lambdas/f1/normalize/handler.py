"""
F1 normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the f1/ prefix (Terraform/s3-raw-data-lake-
notifications.tf). Reads raw Jolpica-F1 JSON written by f1-ingest from
S3, normalizes it into the project schema, and upserts the results into
DynamoDB. Never calls Jolpica directly.

Three prefixes reach DynamoDB, unlike PGA's own normalize Lambda (which
has exactly one, since its single leaderboard fetch already carries
everything -- see library/normalize/f1.py's own module docstring for why
F1 genuinely needs to combine two separate endpoints into one event):

- f1/results/{season}/{round}.json -- the main race. Builds the full
  event + driver/constructor entities, THEN best-effort merges in
  qualifying data if it's already in S3 (it usually isn't yet -- ingest
  writes results before qualifying in the same run, and S3 notifications
  fire per-object, so the qualifying-triggered pass below almost always
  does the actual merge a moment later).
- f1/qualifying/{season}/{round}.json -- re-reads the SAME round's
  results.json (required -- logged and deferred if it's somehow not
  there yet) and re-upserts the event with qualifying now merged in.
  Upserting twice is safe and idempotent, same "every DynamoDB write is
  an upsert" discipline this project already relies on elsewhere.
- f1/sprint/{season}/{round}.json -- a genuinely separate event
  (event_type "sprint", own event_id) for a real Sprint weekend; a
  non-Sprint round's own written sprint.json (ingest/backfill only
  cache a REAL one, but handle a stray/empty one defensively here too)
  is skipped rather than written as a hollow event.

f1/pitstops/ and f1/standings/ stay raw-only (read directly from S3 by
feature engineering, once something reads them at all) -- explicitly
recognized-but-skipped here rather than logged as an unrecognized key
pattern (PGA's own normalize Lambda logs a spurious "unrecognized key"
warning on every one of its daily pga/statistics/*.json writes for
exactly this reason -- its S3 notification filters on the whole pga/
prefix, not just pga/leaderboard/; avoided here on purpose).

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.
"""
import json
import logging
import urllib.parse

import boto3
from botocore.exceptions import ClientError

from library.normalize.f1 import (
    merge_qualifying_into_event,
    race_result_to_constructor_entities,
    race_result_to_driver_entities,
    race_result_to_event_item,
    sprint_result_to_constructor_entities,
    sprint_result_to_driver_entities,
    sprint_result_to_event_item,
)
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("f1-normalize")

SPORT = "f1"
_RAW_ONLY_PREFIXES = ("/pitstops/", "/standings/")

_s3 = boto3.client("s3")
_storage: PipelineStorage | None = None


def _get_storage() -> PipelineStorage:
    # Initialized once per container lifetime, reused across warm invocations.
    global _storage
    if _storage is None:
        _storage = PipelineStorage()
    return _storage


def _read_json(bucket: str, key: str) -> dict:
    response = _s3.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read())


def _try_read_json(bucket: str, key: str) -> dict | None:
    """None (not raised) for an object that genuinely doesn't exist yet
    -- the normal, expected case for a not-yet-ingested COMPANION file
    (qualifying while processing results, or vice versa), not an error.
    Only ever used for that companion lookup, never for the S3 object
    that actually triggered this Lambda invocation (_dispatch reads that
    one via the plain, raising _read_json instead -- a missing
    triggering object would be a genuine, unexpected S3 inconsistency,
    not a normal "hasn't been ingested yet" case). Any other S3 failure
    still raises."""
    try:
        return _read_json(bucket, key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise


def _upsert_race_event(event_item: dict, driver_entities: list[dict], constructor_entities: list[dict]) -> None:
    storage = _get_storage()
    # Constructors upserted before drivers, drivers before the event that
    # references them both -- not load-bearing (DynamoDB has no
    # foreign-key enforcement), but keeps a reader that queries right
    # after this run from ever seeing an event whose participants'
    # entities don't exist yet, same ordering every other normalizer here
    # uses.
    for entity in constructor_entities:
        storage.upsert_entity(entity)
    for entity in driver_entities:
        storage.upsert_entity(entity)
    storage.upsert_event(event_item)


def _process_results(payload: dict, key: str, bucket: str) -> None:
    constructor_entities = race_result_to_constructor_entities(payload, SPORT)
    driver_entities = race_result_to_driver_entities(payload, SPORT)
    event_item = race_result_to_event_item(payload, SPORT)

    qualifying_payload = _try_read_json(bucket, f"f1/qualifying/{event_item['season']}/{event_item['week']}.json")
    merge_qualifying_into_event(event_item, qualifying_payload)

    _upsert_race_event(event_item, driver_entities, constructor_entities)
    logger.info(
        "Upserted 1 event (qualifying %s) and %d entity(ies) (%d driver, %d constructor) from %s",
        "merged" if qualifying_payload else "not yet available",
        len(driver_entities) + len(constructor_entities), len(driver_entities), len(constructor_entities), key,
    )


def _process_qualifying(payload: dict, key: str, bucket: str) -> None:
    race_table = payload.get("MRData", {}).get("RaceTable", {})
    season, round_ = race_table.get("season"), race_table.get("round")
    if season is None or round_ is None:
        logger.warning("Qualifying payload %s has no season/round -- skipping", key)
        return

    # Ingest always writes results.json before qualifying.json in the
    # same run (aws-lambdas/f1/ingest/handler.py's own
    # _fetch_and_write_round), so this should never actually be missing
    # in practice -- logged and deferred rather than guessed at if it
    # somehow is (the results-triggered pass above will pick up this
    # same qualifying file once it exists, so nothing is lost).
    results_payload = _try_read_json(bucket, f"f1/results/{season}/{round_}.json")
    if results_payload is None:
        logger.warning(
            "No results.json yet for season %s round %s -- qualifying merge deferred (%s)", season, round_, key,
        )
        return

    event_item = race_result_to_event_item(results_payload, SPORT)
    merge_qualifying_into_event(event_item, payload)
    _get_storage().upsert_event(event_item)
    logger.info("Merged qualifying into event %s from %s", event_item["event_key"], key)


def _process_sprint(payload: dict, key: str) -> None:
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races or not races[0].get("SprintResults"):
        logger.info("Skipping %s -- not a real Sprint weekend (no SprintResults)", key)
        return

    constructor_entities = sprint_result_to_constructor_entities(payload, SPORT)
    driver_entities = sprint_result_to_driver_entities(payload, SPORT)
    _upsert_race_event(sprint_result_to_event_item(payload, SPORT), driver_entities, constructor_entities)
    logger.info(
        "Upserted 1 sprint event and %d entity(ies) (%d driver, %d constructor) from %s",
        len(driver_entities) + len(constructor_entities), len(driver_entities), len(constructor_entities), key,
    )


def _dispatch(bucket: str, key: str) -> None:
    if "/results/" in key:
        _process_results(_read_json(bucket, key), key, bucket)
        return
    if "/qualifying/" in key:
        _process_qualifying(_read_json(bucket, key), key, bucket)
        return
    if "/sprint/" in key:
        _process_sprint(_read_json(bucket, key), key)
        return
    if any(prefix in key for prefix in _RAW_ONLY_PREFIXES):
        logger.info("Skipping %s -- raw-only prefix, read directly from S3 by feature engineering.", key)
        return
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
