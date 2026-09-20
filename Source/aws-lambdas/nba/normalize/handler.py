"""
NBA normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the nba/ prefix (see
Terraform/s3-raw-data-lake-notifications.tf). Reads raw ESPN JSON written
by nba-ingest/nba-schedule-sync from S3, normalizes it into the project
schema, and upserts the results into DynamoDB. Never calls ESPN directly.

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.

Key routing (based on S3 key pattern). NBA's raw payloads are single
dicts per object (ESPN's site API):
    nba/teams.json               -> team entities
    nba/scoreboard/{date}.json   -> event records
    nba/boxscore/{season}/{event_id}.json -> player stats, player entities, team stats
    nba/roster/{team_id}.json    -> player entities (team_id correction --
                                     see aws-lambdas/nba/ingest/handler.py's
                                     own docstring for why this is fetched
                                     daily)

Reuses library.normalize.espn's shared normalizers directly (no separate
library/normalize/nba.py module): roster_to_player_entities handles NBA's
flat (ungrouped) athletes list, and boxscore_to_player_game_stats handles
NBA's single unnamed stat category without fabricating a "misc" prefix
that would otherwise corrupt TARGET_STAT field names like
"points"/"rebounds". compound_key_splits below is NBA's own map -- one
shared dict for both team- and player-level box scores, since NBA's
"made-attempted" compound keys are identical strings at both levels.
"""
import logging

import boto3

from library.aws import lambda_singletons
from library.aws.boto_config import DEFAULT_CONFIG
from library.normalize import dispatch as dispatch_common
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

# One shared map for both team- and player-level box scores -- NBA's
# "made-attempted" compound stat keys ("fieldGoalsMade-
# fieldGoalsAttempted" etc.) are identical strings at both levels.
# three_pointers_made/three_point_attempts match the sport registry's own
# player-prop TARGET_STAT name exactly (Terraform/dynamodb-sport-
# registry.tf) -- this is what makes that model trainable without a
# separate field-name translation step.
_COMPOUND_KEY_SPLITS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("field_goals_made", "field_goal_attempts"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("three_pointers_made", "three_point_attempts"),
    "freeThrowsMade-freeThrowsAttempted": ("free_throws_made", "free_throw_attempts"),
}

_s3 = boto3.client("s3", config=DEFAULT_CONFIG)
_storage: PipelineStorage | None = None


def _get_storage() -> PipelineStorage:
    return lambda_singletons.get_or_create(globals(), "_storage", PipelineStorage)


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


def _clear_departed_players(storage: PipelineStorage, team_id: str, entities: list[dict], as_of_date: str) -> int:
    """Clears metadata.team_id (and drops the entity out of the
    team-index GSI entirely, by omitting team_key from the rewritten
    item) for any player currently on file for team_id that this fresh
    ESPN roster snapshot no longer lists.

    Same fix, and the same real case that surfaced the need for it
    (2026-09-xx, NCAAFB), as ncaafb/normalize/handler.py's own
    _clear_departed_players -- see that one's docstring for the full
    story. ESPN's own roster response only ever says who IS on the team
    right now; upserting only ever adds/refreshes players actually
    present in a fresh fetch, never removes one who's disappeared from
    it, so a retired, waived, or traded-and-not-yet-signed player
    otherwise keeps their last-confirmed team_id forever -- still
    showing up as a current-team stat leader long after they're gone.

    A genuinely empty `entities` list is left alone entirely (returns 0
    without querying anything) -- a transient ESPN API gap for this one
    team is far more likely than every player on a real roster leaving
    at once, and treating it as "everyone left" would wipe the whole
    team's roster attribution over one bad fetch instead of just sitting
    stale until tomorrow's re-fetch corrects it."""
    if not entities:
        return 0
    present_ids = {entity["entity_id"] for entity in entities}
    cleared = 0
    for on_file in storage.get_team_entities(SPORT, team_id):
        entity_id = on_file.get("entity_id")
        if entity_id is None or entity_id in present_ids:
            continue
        metadata = dict(on_file.get("metadata") or {})
        metadata.pop("team_id", None)
        metadata["team_id_as_of"] = as_of_date
        cleared_entity = {k: v for k, v in on_file.items() if k != "team_key"}
        cleared_entity["metadata"] = metadata
        if storage.upsert_player_entity(cleared_entity):
            cleared += 1
    return cleared


def _process_roster(payload: dict, key: str) -> None:
    storage = _get_storage()
    entities = roster_to_player_entities(payload, SPORT)
    for entity in entities:
        storage.upsert_player_entity(entity)
    team_id = str(payload["team"]["id"])
    as_of_date = payload["timestamp"][:10]
    cleared = _clear_departed_players(storage, team_id, entities, as_of_date)
    logger.info("Upserted %d player entities (%d cleared as no longer rostered) from %s", len(entities), cleared, key)


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
    dispatch_common.dispatch(
        _s3, bucket, key, logger,
        process_teams=_process_teams, process_scoreboard=_process_scoreboard,
        process_boxscore=_process_boxscore, process_roster=_process_roster,
    )


def lambda_handler(event: dict, context) -> dict:
    return dispatch_common.lambda_handler_body(event, _dispatch, logger)
