"""
One-time migration: adds the `team_key` attribute to every existing
player entity row written before the entities table's team-index GSI
existed (see Terraform/dynamodb-entities.tf). team_id was always stored
in metadata.team_id; team_key just makes it queryable by
FeatureStorage.get_team_entities (live_features.py's roster-driven
presumptive-leader selection -- see that module's own docstring).

Reads ONLY from DynamoDB -- never contacts ESPN. Every row already has
what's needed to derive team_key (its own sport + metadata.team_id), this
just copies it out and writes it back as a real attribute.

Idempotent and safe to interrupt or re-run: skips any row that already
has `team_key` set, and any row with no metadata.team_id at all (a team
entity, or a player never associated with a team -- neither belongs in
the team-index). DynamoDBTable.batch_write's overwrite-by-key semantics
mean every other field on a patched row is written back exactly as
scanned -- this only ever adds the one new field, nothing else changes.

Required environment variables:
    ENTITIES_TABLE_NAME
    AWS_REGION

Usage:
    python migrations/backfill_entity_team_key.py
    python migrations/backfill_entity_team_key.py --dry-run
"""
import argparse
import logging
import os

from library.aws.dynamodb_table import DynamoDBTable
from library.schema.keys import entity_team_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("backfill-entity-team-key")


def migrate(region: str | None, dry_run: bool) -> None:
    table_name = os.environ["ENTITIES_TABLE_NAME"]
    table = DynamoDBTable(table_name, region=region)

    logger.info("Scanning %s for player rows missing `team_key`...", table_name)
    rows = table.scan()
    missing = [
        row for row in rows
        if "team_key" not in row and row.get("metadata", {}).get("team_id")
    ]
    logger.info("%s: %d rows total, %d missing `team_key`", table_name, len(rows), len(missing))

    if not missing:
        return
    if dry_run:
        logger.info("Dry run -- not writing. Example row that would be patched: %s", missing[0])
        return

    for row in missing:
        row["team_key"] = entity_team_key(row["sport"], row["metadata"]["team_id"])
    table.batch_write(missing, key_names=["entity_key"])
    logger.info("%s: patched %d rows", table_name, len(missing))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time migration: adds the `team_key` attribute to existing entity rows.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report how many rows would be patched without writing anything.",
    )
    args = parser.parse_args()

    migrate(os.environ.get("AWS_REGION"), args.dry_run)


if __name__ == "__main__":
    main()
