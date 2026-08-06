"""
One-time migration: corrects every player entity's metadata.team_id to
reflect their most recent (by event_date) player_game_stats row.
PipelineStorage.upsert_player_entity now guards future writes against
this going stale (see that method's own docstring), but that guard
doesn't retroactively fix whatever's already wrong in DynamoDB -- this
script does that one-time correction.

Reads player_game_stats (via its sport-index GSI) to derive each player's
correct current team_id, then patches just that field (plus the new
team_id_as_of) on their existing entity record -- name/jersey/every other
field is left untouched.

Idempotent and safe to interrupt or re-run.

Required environment variables:
    PLAYER_GAME_STATS_TABLE_NAME
    ENTITIES_TABLE_NAME
    AWS_REGION

Usage:
    python migrations/fix_stale_player_team_ids.py
    python migrations/fix_stale_player_team_ids.py --dry-run
    python migrations/fix_stale_player_team_ids.py --sport nfl
"""
import argparse
import logging
import os

from boto3.dynamodb.conditions import Key

from library.aws.dynamodb_table import DynamoDBTable
from library.schema.keys import entity_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("fix-stale-player-team-ids")


def _most_recent_team_by_player(player_game_stats_table: DynamoDBTable, sport: str) -> dict[str, tuple[str, str]]:
    """entity_id -> (team_id, event_date) for whichever row is that
    player's own latest event_date. Queries the sport-index GSI rather
    than scanning the whole table -- same access pattern
    FeatureStorage.get_all_player_game_stats uses."""
    rows = player_game_stats_table.query(Key("sport").eq(sport), index_name="sport-index")
    latest: dict[str, tuple[str, str]] = {}
    for row in rows:
        entity_id = row.get("entity_id")
        event_date = row.get("event_date", "")
        team_id = row.get("team_id")
        if entity_id is None or team_id is None:
            continue
        current = latest.get(entity_id)
        if current is None or event_date > current[1]:
            latest[entity_id] = (team_id, event_date)
    return latest


def migrate(sport: str, region: str | None, dry_run: bool) -> None:
    player_game_stats_table = DynamoDBTable(os.environ["PLAYER_GAME_STATS_TABLE_NAME"], region=region)
    entities_table = DynamoDBTable(os.environ["ENTITIES_TABLE_NAME"], region=region)

    logger.info("Deriving each player's most recent team from player_game_stats...")
    correct_team_by_player = _most_recent_team_by_player(player_game_stats_table, sport)
    logger.info("Derived current team for %d players", len(correct_team_by_player))

    patched = 0
    for entity_id, (correct_team_id, event_date) in correct_team_by_player.items():
        entity = entities_table.get_item({"entity_key": entity_key(sport, entity_id)})
        if entity is None:
            continue  # player_game_stats row with no matching entity -- nothing to patch
        metadata = entity.get("metadata", {})
        if metadata.get("team_id") == correct_team_id and metadata.get("team_id_as_of") == event_date:
            continue

        if dry_run:
            logger.info(
                "Would patch %s: team_id %r -> %r (as_of %s)",
                entity_id, metadata.get("team_id"), correct_team_id, event_date,
            )
            patched += 1
            continue

        metadata["team_id"] = correct_team_id
        metadata["team_id_as_of"] = event_date
        entity["metadata"] = metadata
        entities_table.put_item(entity)
        patched += 1

    action = "Would patch" if dry_run else "Patched"
    logger.info("%s %d player entities", action, patched)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time migration: corrects player entities' metadata.team_id "
        "to reflect their most recent player_game_stats row.",
    )
    parser.add_argument("--sport", default="nfl", help="Sport to migrate (default: nfl).")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report how many entities would be patched without writing anything.",
    )
    args = parser.parse_args()

    migrate(args.sport, os.environ.get("AWS_REGION"), args.dry_run)


if __name__ == "__main__":
    main()
