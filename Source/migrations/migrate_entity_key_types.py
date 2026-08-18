"""
One-time migration: rewrites every entities-table row's entity_key from
the old type-less format (SPORT#<sport>#ENTITY#<id>) onto the new
type-scoped one (SPORT#<sport>#ENTITY#<TEAM|PLAYER>#<id>) that
library.schema.keys.entity_key now builds. See project-nba-entity-
collision memory for the bug this fixes: the old key format let a team
and a player share one row whenever their raw ESPN ids matched (confirmed
real -- NBA team id 25, Oklahoma City, was silently holding athlete id
25, Metta World Peace, before this).

MUST run only after the new entity_key code is deployed and has had a
chance to write at least one fresh row under the new format -- otherwise
an old-code write racing this migration would just recreate the
type-less key this script just deleted. This migration only moves what's
CURRENTLY stored at each old-format key; it can't recover a row that was
already overwritten by the collision before this ran (e.g. Oklahoma
City's own team data is gone from this table, not just misfiled -- the
current occupant of that old key is whichever entity wrote last). That
side repopulates on its own once normalize's next scheduled run
re-upserts every team from its source feed under the new, now-collision-
free key -- nothing this script needs to do.

Reads and writes ONLY the entities table -- never contacts ESPN/CFBD.

Idempotent and safe to interrupt or re-run: an item whose entity_key is
already 5 segments (already migrated) is skipped. A migrated item is
written under its new key, then the old-key item is deleted -- if
interrupted between those two steps, re-running just repeats the
(harmless, overwrite-safe) write and finishes the delete.

Required environment variables:
    ENTITIES_TABLE_NAME
    AWS_REGION

Usage:
    python migrations/migrate_entity_key_types.py
    python migrations/migrate_entity_key_types.py --dry-run
"""
import argparse
import logging
import os

from library.aws.dynamodb_table import DynamoDBTable
from library.schema.keys import entity_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("migrate-entity-key-types")


def _is_old_format(key: str) -> bool:
    # New format: SPORT#<SPORT>#ENTITY#<TEAM|PLAYER>#<id> -- 5 segments.
    # Old format: SPORT#<SPORT>#ENTITY#<id> -- 4. An id itself may contain
    # "#" (unseen in practice, but not worth trusting) -- split with a
    # maxsplit of 4 so a stray "#" inside an id can't be mistaken for the
    # type segment.
    return len(key.split("#", 4)) == 4


def migrate(region: str | None, dry_run: bool) -> None:
    table_name = os.environ["ENTITIES_TABLE_NAME"]
    table = DynamoDBTable(table_name, region=region)

    logger.info("Scanning %s for old-format entity_key rows...", table_name)
    rows = table.scan()
    stale = [row for row in rows if _is_old_format(row.get("entity_key", ""))]
    logger.info("%s: %d rows total, %d on the old key format", table_name, len(rows), len(stale))

    if not stale:
        return
    if dry_run:
        logger.info("Dry run -- not writing. Example row that would be migrated: %s", stale[0])
        return

    migrated, skipped = 0, 0
    for row in stale:
        entity_type = row.get("entity_type")
        sport = row.get("sport")
        entity_id = row.get("entity_id")
        if not entity_type or not sport or not entity_id:
            logger.warning("Skipping row missing sport/entity_id/entity_type: %s", row)
            skipped += 1
            continue

        old_key = row["entity_key"]
        new_row = {**row, "entity_key": entity_key(sport, entity_id, entity_type)}
        table.put_item(new_row)
        table.delete_item({"entity_key": old_key})
        migrated += 1

    logger.info("%s: migrated %d rows, skipped %d", table_name, migrated, skipped)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time migration: moves existing entities-table rows onto the "
        "new type-scoped entity_key format (SPORT#<sport>#ENTITY#<TEAM|PLAYER>#<id>).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report how many rows would be migrated without writing anything.",
    )
    args = parser.parse_args()

    region = os.environ.get("AWS_REGION")
    migrate(region, args.dry_run)


if __name__ == "__main__":
    main()
