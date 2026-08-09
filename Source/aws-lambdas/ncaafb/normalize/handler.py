"""
NCAAFB normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the ncaafb/ prefix (see
Terraform/s3-raw-data-lake-notifications.tf). Reads raw CFBD JSON written
by ncaafb-ingest/ncaafb-schedule-sync from S3, normalizes it into the
project schema, and upserts the results into DynamoDB. Never calls CFBD
directly.

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.

Key routing (based on S3 key pattern) -- every CFBD raw object here is a
JSON list (one entry per game), unlike NFL's own scoreboard/boxscore
objects which are each a single dict, since CFBD's box score endpoints
are bulk-per-week rather than per-game:
    ncaafb/games/{season}/{season_type}/{week}.json     -> event records
    ncaafb/boxscore/{season}/{season_type}/{week}.json  -> player stats, player entities
    ncaafb/teamstats/{season}/{season_type}/{week}.json -> team stats
"""
import json
import logging
import urllib.parse

import boto3

from library.normalize.ncaafb import (
    game_player_stats_to_player_game_stats,
    game_team_stats_to_team_game_stats,
    game_to_event_item,
)
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
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


def _process_games(payload: list, key: str) -> None:
    storage = _get_storage()
    for game in payload:
        storage.upsert_event(game_to_event_item(game, SPORT))
    logger.info("Upserted %d events from %s", len(payload), key)


def _process_boxscore(payload: list, key: str) -> None:
    storage = _get_storage()
    total_stats = total_entities = 0
    for game_box_score in payload:
        stats_items, player_entities = game_player_stats_to_player_game_stats(game_box_score, SPORT)
        for entity in player_entities:
            storage.upsert_player_entity(entity)
        storage.write_player_game_stats(stats_items)
        total_stats += len(stats_items)
        total_entities += len(player_entities)
    logger.info("Wrote %d player stat lines and %d player entities from %s", total_stats, total_entities, key)


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
