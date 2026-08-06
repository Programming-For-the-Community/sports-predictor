"""
One-time migration: re-derives every event item from its already-stored
raw scoreboard JSON so existing events pick up kickoff_time (added to
scoreboard_event_to_event_item after most events were already normalized).

Reads ONLY from S3 -- never contacts ESPN. Every nfl/scoreboard/{season}/
{type}/{week}.json object already carries ESPN's full kickoff timestamp;
scoreboard_event_to_event_item just wasn't keeping it. Re-running it
against what's already stored reproduces every other field exactly (coach/
injury/depth-chart included, for whichever weeks' raw JSON already has
them from ingest's own enrichment) and adds kickoff_time on top.

Idempotent and safe to interrupt or re-run: upsert_event overwrites by key,
so re-processing an already-migrated week just writes the same values again.

Required environment variables:
    RAW_BUCKET_NAME
    EVENTS_TABLE_NAME
    AWS_REGION

Usage:
    python migrations/backfill_kickoff_time.py
    python migrations/backfill_kickoff_time.py --dry-run
"""
import argparse
import logging
import os

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.normalize.espn import scoreboard_event_to_event_item

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("backfill-kickoff-time")

SPORT = "nfl"
SCOREBOARD_PREFIX = "nfl/scoreboard/"


def migrate(s3: S3Manager, events_table: DynamoDBTable, dry_run: bool) -> None:
    keys = s3.list_keys(SCOREBOARD_PREFIX)
    logger.info("Found %d scoreboard objects under %s", len(keys), SCOREBOARD_PREFIX)

    patched = 0
    for key in keys:
        scoreboard = s3.get_json(key)
        events = scoreboard.get("events", [])
        for event in events:
            item = scoreboard_event_to_event_item(event, SPORT)
            if dry_run:
                logger.info("Would upsert %s (kickoff_time=%s)", item["event_key"], item.get("kickoff_time"))
            else:
                events_table.put_item(item)
            patched += 1

    action = "Would patch" if dry_run else "Patched"
    logger.info("%s %d events across %d scoreboard objects", action, patched, len(keys))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time migration: adds kickoff_time to existing events from already-stored raw scoreboard JSON.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report how many events would be patched without writing anything.",
    )
    args = parser.parse_args()

    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(os.environ["RAW_BUCKET_NAME"], region=region)
    events_table = DynamoDBTable(os.environ["EVENTS_TABLE_NAME"], region=region)
    migrate(s3, events_table, args.dry_run)


if __name__ == "__main__":
    main()
