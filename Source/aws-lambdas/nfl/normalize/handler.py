"""
NFL normalize Lambda. Triggered by S3 PutObject events on the raw data
lake (filtered to the nfl/ prefix -- see Terraform/lambda-nfl-normalize.tf).
Reads raw ESPN JSON from S3, normalizes it to the project schema, and
upserts the results into DynamoDB. Never fetches from ESPN directly.

One Lambda invocation may receive multiple S3 records if notifications are
batched, though in practice a single PUT triggers a single notification.
Each record is processed independently so a failure in one doesn't block
the others.

Key routing (based on S3 key pattern):
    nfl/teams.json                             -> team entities
    nfl/scoreboard/{season}/{type}/{week}.json -> event records
    nfl/boxscore/{season}/{event_id}.json      -> player stats, player entities, team stats
"""
import json
import logging
import urllib.parse

import boto3

from library.normalize.espn import (
    boxscore_to_player_game_stats,
    boxscore_to_team_game_stats,
    scoreboard_event_to_event_item,
    team_to_entity,
)
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-normalize")

SPORT = "nfl"

_COMPOUND_KEY_SPLITS: dict[str, tuple[str, str]] = {
    "completions/passingAttempts": ("completions", "passing_attempts"),
    "sacks-sackYardsLost": ("sacks_taken", "sack_yards_lost"),
    "fieldGoalsMade/fieldGoalAttempts": ("field_goals_made", "field_goal_attempts"),
    "extraPointsMade/extraPointAttempts": ("extra_points_made", "extra_point_attempts"),
}

_TEAM_COMPOUND_KEY_SPLITS: dict[str, tuple[str, str]] = {
    "thirdDownEff": ("third_down_conversions", "third_down_attempts"),
    "fourthDownEff": ("fourth_down_conversions", "fourth_down_attempts"),
    "completionAttempts": ("completions", "pass_attempts"),
    "redZoneAttempts": ("red_zone_conversions", "red_zone_attempts"),
    "sacksYardsLost": ("sacks_taken", "sack_yards_lost"),
    "totalPenaltiesYards": ("penalties", "penalty_yards"),
}

_s3 = boto3.client("s3")
_storage: PipelineStorage | None = None


def _get_storage() -> PipelineStorage:
    # Initialized once per container lifetime, reused across warm invocations.
    global _storage
    if _storage is None:
        _storage = PipelineStorage()
    return _storage


def _process_teams(payload: dict, key: str) -> None:
    storage = _get_storage()
    league = payload["sports"][0]["leagues"][0]
    for team_entry in league["teams"]:
        storage.upsert_entity(team_to_entity(team_entry["team"], SPORT))
    logger.info("Upserted %d team entities from %s", len(league["teams"]), key)


def _process_scoreboard(payload: dict, key: str) -> None:
    storage = _get_storage()
    events = payload.get("events", [])
    for event in events:
        storage.upsert_event(scoreboard_event_to_event_item(event, SPORT))
    logger.info("Upserted %d events from %s", len(events), key)


def _process_boxscore(payload: dict, key: str) -> None:
    storage = _get_storage()
    stats_items, player_entities = boxscore_to_player_game_stats(payload, SPORT, _COMPOUND_KEY_SPLITS)
    for entity in player_entities:
        storage.upsert_entity(entity)
    storage.write_player_game_stats(stats_items)
    logger.info(
        "Wrote %d player stat lines and %d player entities from %s",
        len(stats_items), len(player_entities), key,
    )

    team_stats_items = boxscore_to_team_game_stats(payload, SPORT, _TEAM_COMPOUND_KEY_SPLITS)
    storage.write_team_game_stats(team_stats_items)
    logger.info("Wrote %d team stat lines from %s", len(team_stats_items), key)


def _dispatch(bucket: str, key: str) -> None:
    response = _s3.get_object(Bucket=bucket, Key=key)
    payload = json.loads(response["Body"].read())

    if key.endswith("/teams.json"):
        _process_teams(payload, key)
    elif "/scoreboard/" in key:
        _process_scoreboard(payload, key)
    elif "/boxscore/" in key:
        _process_boxscore(payload, key)
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