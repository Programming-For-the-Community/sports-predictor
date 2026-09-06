"""
NCAA MBB normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the ncaambb/ prefix (see
Terraform/s3-raw-data-lake-notifications.tf). Reads raw ESPN JSON written
by ncaambb-ingest/ncaambb-schedule-sync from S3, normalizes it into the
project schema, and upserts the results into DynamoDB. Never calls ESPN
directly.

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.

Key routing (based on S3 key pattern). NCAA MBB's raw payloads are single
dicts per object (ESPN's site API), same shape as NBA's:
    ncaambb/teams.json               -> team entities
    ncaambb/scoreboard/{date}.json   -> event records
    ncaambb/boxscore/{season}/{event_id}.json -> player stats, player entities, team stats
    ncaambb/roster/{team_id}.json    -> player entities (team_id correction --
                                         see aws-lambdas/ncaambb/ingest/handler.py's
                                         own docstring for why this is fetched
                                         daily)

Reuses library.normalize.espn's shared normalizers directly (no separate
library/normalize/ncaambb.py module): roster_to_player_entities handles
NCAA MBB's flat (ungrouped) athletes list (same shape as NBA's, not
NFL's grouped shape), and boxscore_to_player_game_stats handles NCAA
MBB's single unnamed stat category without fabricating a "misc" prefix
that would otherwise corrupt TARGET_STAT field names like
"points"/"rebounds". compound_key_splits below is NCAA MBB's own map --
uses the exact same raw ESPN stat keys as NBA's own box score
("fieldGoalsMade-fieldGoalsAttempted" etc.), so the values are identical
to NBA's, but this stays its own dict (not imported from nba/normalize)
per this project's per-sport duplication convention for sport-specific
assembly.
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
logger = logging.getLogger("ncaambb-normalize")

SPORT = "ncaambb"

# One shared map for both team- and player-level box scores -- NCAA MBB's
# "made-attempted" compound stat keys are identical strings to NBA's own.
# three_pointers_made/three_point_attempts
# match the sport registry's own player-prop TARGET_STAT name exactly
# (Terraform/dynamodb-sport-registry.tf) -- this is what makes that model
# trainable without a separate field-name translation step.
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
    it, so a departed player otherwise keeps their last-confirmed team_id
    forever. NCAA MBB already re-fetches every team's own roster daily
    (see ingest/handler.py's own docstring), so this closes the gap on
    essentially the very next run, unlike NCAAFB's ~monthly cadence.

    A genuinely empty `entities` list is left alone entirely (returns 0
    without querying anything) -- a transient ESPN API gap for this one
    team is far more likely than every player on a real D1 roster leaving
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
