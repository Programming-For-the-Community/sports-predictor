"""
One-time migration: adds the `sport` attribute to every existing
player_game_stats/team_game_stats row written before
library.normalize.espn's boxscore_to_player_game_stats/
boxscore_to_team_game_stats started storing it directly. sport was always
derivable from a row's own event_key (SPORT#<SPORT>#EVENT#...), just never
stored as its own attribute until the sport-index GSI needed it (see
Terraform/dynamodb-player-game-stats.tf, dynamodb-team-game-stats.tf).

Reads ONLY from DynamoDB -- never contacts ESPN. This is what lets the
sport-index GSI go live without re-running the historical NFL backfill:
every row already carries what's needed to derive `sport`, this just
copies it out (library.schema.keys.sport_from_event_key) and writes it
back as a real attribute so FeatureStorage.get_all_player_game_stats/
get_all_team_game_stats can Query the GSI instead of scanning the whole
table.

Idempotent and safe to interrupt or re-run: skips any row that already
has `sport` set. DynamoDBTable.batch_write's overwrite-by-key semantics
mean every other field on a patched row is written back exactly as
scanned -- this only ever adds the one new field, nothing else changes.

Required environment variables:
    PLAYER_GAME_STATS_TABLE_NAME
    TEAM_GAME_STATS_TABLE_NAME
    AWS_REGION

Usage:
    python migrations/backfill_sport_attribute.py
    python migrations/backfill_sport_attribute.py --dry-run
    python migrations/backfill_sport_attribute.py --table player_game_stats
"""
import argparse
import logging
import os

from library.aws.dynamodb_table import DynamoDBTable
from library.schema.keys import sport_from_event_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("backfill-sport-attribute")

# table label -> (env var naming its DynamoDB table, primary key attribute
# names for DynamoDBTable.batch_write's overwrite-by-key semantics).
TABLES = {
    "player_game_stats": ("PLAYER_GAME_STATS_TABLE_NAME", ["event_key", "player_key"]),
    "team_game_stats": ("TEAM_GAME_STATS_TABLE_NAME", ["event_key", "team_key"]),
}


def migrate_table(table_name_env: str, key_names: list[str], region: str | None, dry_run: bool) -> None:
    table_name = os.environ[table_name_env]
    table = DynamoDBTable(table_name, region=region)

    logger.info("Scanning %s for rows missing `sport`...", table_name)
    rows = table.scan()
    missing = [row for row in rows if "sport" not in row]
    logger.info("%s: %d rows total, %d missing `sport`", table_name, len(rows), len(missing))

    if not missing:
        return
    if dry_run:
        logger.info("Dry run -- not writing. Example row that would be patched: %s", missing[0])
        return

    for row in missing:
        row["sport"] = sport_from_event_key(row["event_key"])
    table.batch_write(missing, key_names=key_names)
    logger.info("%s: patched %d rows", table_name, len(missing))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time migration: adds the `sport` attribute to existing "
        "player_game_stats/team_game_stats rows.",
    )
    parser.add_argument(
        "--table", choices=list(TABLES), default=None,
        help="Migrate only this table -- default runs both.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report how many rows would be patched without writing anything.",
    )
    args = parser.parse_args()

    region = os.environ.get("AWS_REGION")
    targets = [args.table] if args.table else list(TABLES)
    for name in targets:
        table_name_env, key_names = TABLES[name]
        migrate_table(table_name_env, key_names, region, args.dry_run)


if __name__ == "__main__":
    main()
