"""
NCAAFB normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the ncaafb/ prefix. Reads raw CFBD JSON written by
ncaafb-ingest/ncaafb-schedule-sync from S3, normalizes it into the
project schema, and upserts the results into DynamoDB. Never calls CFBD
directly.

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.

Key routing (based on S3 key pattern) -- every CFBD raw object here is a
JSON list (one entry per game), since CFBD's box score endpoints are
bulk-per-week rather than per-game:
    ncaafb/games/{season}/{season_type}/{week}.json     -> event records
    ncaafb/boxscore/{season}/{season_type}/{week}.json  -> player stats, player entities
    ncaafb/teamstats/{season}/{season_type}/{week}.json -> team stats
    ncaafb/roster/{season}.json                         -> player entities (position)
                                                            -- a {"fetched_at", "data"}
                                                            envelope, not a bare list

Also runs _cancel_stale_scheduled_events unconditionally on every
invocation, regardless of which key(s) triggered it.
"""
import concurrent.futures
import json
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone

import boto3

from library.normalize.ncaafb import (
    game_player_stats_to_player_game_stats,
    game_team_stats_to_team_game_stats,
    game_to_event_item,
    roster_to_player_entities,
)
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("ncaafb-normalize")

SPORT = "ncaafb"

_s3 = boto3.client("s3")
_storage: PipelineStorage | None = None


def _get_storage() -> PipelineStorage:
    # Initialized once per container lifetime, reused across warm invocations.
    global _storage
    if _storage is None:
        _storage = PipelineStorage()
    return _storage


STALE_UNPLAYED_MAX_AGE_DAYS = 365


def _cancel_stale_scheduled_events(storage: PipelineStorage) -> int:
    """Corrects any event still "scheduled" more than a year past its own
    event_date to "canceled". CFBD's bulk /games endpoint has no
    structured cancellation signal of its own, so a genuinely
    canceled/postponed NCAAFB game just sits at "scheduled" forever
    without this; a year is long enough that no real delayed/rescheduled
    game could still be legitimately upcoming. Run once per invocation
    (not per S3 record) -- idempotent and cheap (one sport-status-index
    Query). Returns how many rows were corrected, purely for logging."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=STALE_UNPLAYED_MAX_AGE_DAYS)).isoformat()
    stale = [e for e in storage.get_events_by_status(SPORT, "scheduled") if e.get("event_date", "") < cutoff]
    for event in stale:
        event["status"] = "canceled"
        storage.upsert_event(event)
    return len(stale)


def _process_games(payload: list, key: str) -> None:
    storage = _get_storage()
    for game in payload:
        storage.upsert_event(game_to_event_item(game, SPORT))
    logger.info("Upserted %d events from %s", len(payload), key)


def _preserve_roster_position(storage: PipelineStorage, entity: dict) -> None:
    """CFBD's box-score athletes carry no position -- every entity item
    game_player_stats_to_player_game_stats produces has
    metadata.position=None. Without this, upsert_player_entity's
    full-item PutItem would blank out whatever position roster sync
    (_process_roster below) already set, every time that player's box
    score line gets reprocessed, since a box score's own team_id_as_of
    (that week's game date) is always newer than the last roster sync's
    -- exactly the condition upsert_player_entity's staleness guard lets
    win. Best-effort: a lookup failure just leaves position None for
    this write."""
    if entity["metadata"]["position"] is not None:
        return
    try:
        existing = storage.get_entity(SPORT, entity["entity_id"], "player")
    except Exception:
        logger.exception("Failed looking up existing entity %s for position preservation", entity["entity_id"])
        return
    if existing and (existing.get("metadata") or {}).get("position") is not None:
        entity["metadata"]["position"] = existing["metadata"]["position"]


# Both player-entity write paths below fan writes out across threads
# rather than looping serially. Each write is one (sometimes two, see
# _write_boxscore_entity) synchronous DynamoDB round-trip; at CFBD's real
# scale (a week's box score can be 1000+ entities, a full-season roster
# ~16k rows) serial writes would blow Lambda's timeout. I/O-bound work,
# so GIL contention isn't a concern.
WRITE_WORKERS = 20


def _write_boxscore_entity(storage: PipelineStorage, entity: dict) -> bool:
    _preserve_roster_position(storage, entity)
    return storage.upsert_player_entity(entity)


def _process_boxscore(payload: list, key: str) -> None:
    storage = _get_storage()
    total_stats = 0
    all_entities: list[dict] = []
    for game_box_score in payload:
        stats_items, player_entities = game_player_stats_to_player_game_stats(game_box_score, SPORT)
        storage.write_player_game_stats(stats_items)
        total_stats += len(stats_items)
        all_entities.extend(player_entities)

    with concurrent.futures.ThreadPoolExecutor(max_workers=WRITE_WORKERS) as pool:
        list(pool.map(lambda entity: _write_boxscore_entity(storage, entity), all_entities))

    logger.info("Wrote %d player stat lines and %d player entities from %s", total_stats, len(all_entities), key)


def _process_roster(payload: dict, key: str) -> None:
    # Split written vs. rejected -- roster's own team_id_as_of is always
    # today's fetch date, so a rejection here means something else
    # already wrote a same-day-or-newer team_id_as_of for that same
    # entity_id first. A candidate count of 0 written entities means
    # roster_to_player_entities itself found nothing to write (e.g.
    # every entry missing "id"/"teamId") -- a different failure mode
    # than a nonzero rejected count.
    storage = _get_storage()
    as_of_date = payload["fetched_at"][:10]
    entities = roster_to_player_entities(payload.get("data", []), SPORT, as_of_date)
    with concurrent.futures.ThreadPoolExecutor(max_workers=WRITE_WORKERS) as pool:
        written = sum(pool.map(storage.upsert_player_entity, entities))
    rejected = len(entities) - written
    logger.info(
        "Wrote %d player entities (%d rejected by the staleness guard) from %s -- %d candidates from %d raw roster rows",
        written, rejected, key, len(entities), len(payload.get("data", [])),
    )


def _process_teamstats(payload: list, key: str) -> None:
    storage = _get_storage()
    total = 0
    for game_team_box_score in payload:
        items = game_team_stats_to_team_game_stats(game_team_box_score, SPORT)
        storage.write_team_game_stats(items)
        total += len(items)
    logger.info("Wrote %d team stat lines from %s", total, key)


def _dispatch(bucket: str, key: str) -> None:
    response = _s3.get_object(Bucket=bucket, Key=key)
    payload = json.loads(response["Body"].read())

    if "/games/" in key:
        _process_games(payload, key)
    elif "/boxscore/" in key:
        _process_boxscore(payload, key)
    elif "/teamstats/" in key:
        _process_teamstats(payload, key)
    elif "/roster/" in key:
        _process_roster(payload, key)
    else:
        logger.warning("Unrecognized S3 key pattern, skipping: %s", key)


def lambda_handler(event: dict, context) -> dict:
    records = event.get("Records", [])
    processed = failed = 0

    try:
        canceled = _cancel_stale_scheduled_events(_get_storage())
        if canceled:
            logger.info("Canceled %d stale scheduled event(s)", canceled)
    except Exception:
        logger.exception("Failed cancelling stale scheduled events")

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
