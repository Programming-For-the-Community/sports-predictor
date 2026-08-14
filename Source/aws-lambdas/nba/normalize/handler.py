"""
NBA normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the nba/ prefix (see
Terraform/s3-raw-data-lake-notifications.tf). Reads raw ESPN JSON written
by nba-ingest/nba-schedule-sync from S3, normalizes it into the project
schema, and upserts the results into DynamoDB. Never calls ESPN directly.

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.

Key routing (based on S3 key pattern), mirroring NFL's own normalize --
NBA's raw payloads are single dicts per object (ESPN's site API, same as
NFL), unlike NCAAFB's CFBD-sourced bulk-per-week lists:
    nba/teams.json               -> team entities
    nba/scoreboard/{date}.json   -> event records
    nba/boxscore/{season}/{event_id}.json -> player stats, player entities, team stats
    nba/roster/{team_id}.json    -> player entities (team_id correction --
                                     see aws-lambdas/nba/ingest/handler.py's
                                     own docstring for why this is fetched
                                     daily)

Reuses library.normalize.espn's shared normalizers directly (no separate
library/normalize/nba.py module) -- NBA's box-score/scoreboard/roster
response shapes were confirmed live (2026-08-13/14, see
project-nba-onboarding memory) to match NFL's site-API shapes closely
enough that only two things needed generalizing in the shared module
itself, not forking: roster_to_player_entities now handles NBA's flat
(ungrouped) athletes list alongside NFL's position-grouped one, and
boxscore_to_player_game_stats now handles NBA's single unnamed stat
category (vs NFL's named passing/rushing/receiving categories) without
fabricating a "misc" prefix that would otherwise corrupt TARGET_STAT
field names like "points"/"rebounds". compound_key_splits below is NBA's
own map (one shared dict for both team- and player-level box scores,
unlike NFL's two separate maps, since NBA's "made-attempted" compound
keys are identical strings at both levels).
"""
import json
import logging
import urllib.parse

import boto3

from library.normalize.espn import (
    boxscore_to_player_game_stats,
    boxscore_to_team_game_stats,
    roster_to_player_entities,
    scoreboard_event_to_event_item,
    team_to_entity,
)
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nba-normalize")

SPORT = "nba"

# One shared map for both team- and player-level box scores -- confirmed
# live, 2026-08-13/14, that NBA's "made-attempted" compound stat keys
# ("fieldGoalsMade-fieldGoalsAttempted" etc.) are identical strings at
# both levels, unlike NFL's own two separate maps. three_pointers_made/
# three_point_attempts match the sport registry's own player-prop
# TARGET_STAT name exactly (Terraform/dynamodb-sport-registry.tf) -- this
# is what makes that model trainable without a separate field-name
# translation step.
_COMPOUND_KEY_SPLITS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("field_goals_made", "field_goal_attempts"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("three_pointers_made", "three_point_attempts"),
    "freeThrowsMade-freeThrowsAttempted": ("free_throws_made", "free_throw_attempts"),
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


def _process_roster(payload: dict, key: str) -> None:
    storage = _get_storage()
    entities = roster_to_player_entities(payload, SPORT)
    for entity in entities:
        storage.upsert_player_entity(entity)
    logger.info("Upserted %d player entities from %s", len(entities), key)


def _process_boxscore(payload: dict, key: str) -> None:
    storage = _get_storage()
    stats_items, player_entities = boxscore_to_player_game_stats(payload, SPORT, _COMPOUND_KEY_SPLITS)
    for entity in player_entities:
        storage.upsert_player_entity(entity)
    storage.write_player_game_stats(stats_items)
    logger.info(
        "Wrote %d player stat lines and %d player entities from %s",
        len(stats_items), len(player_entities), key,
    )

    team_stats_items = boxscore_to_team_game_stats(payload, SPORT, _COMPOUND_KEY_SPLITS)
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
    elif "/roster/" in key:
        _process_roster(payload, key)
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
