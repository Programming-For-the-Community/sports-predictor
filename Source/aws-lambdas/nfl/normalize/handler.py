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
    nfl/roster/{team_id}.json                  -> player entities (team_id correction)
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
    it, so a retired or released player otherwise keeps their last-
    confirmed team_id forever -- still showing up as a current-team stat
    leader long after they're gone.

    A genuinely empty `entities` list is left alone entirely (returns 0
    without querying anything) -- a transient ESPN API gap for this one
    team is far more likely than every player on a real 53-man roster
    leaving at once, and treating it as "everyone left" would wipe the
    whole team's roster attribution over one bad fetch instead of just
    sitting stale until tomorrow's re-fetch corrects it."""
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

    team_stats_items = boxscore_to_team_game_stats(payload, SPORT, _TEAM_COMPOUND_KEY_SPLITS)
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