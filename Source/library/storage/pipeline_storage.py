"""
Shared S3 + DynamoDB wiring for a sport's ingest/backfill pipeline. Every
sport reads and writes the same shared tables (see
design/DATA_SCHEMA.md) -- entities, events, player_game_stats, and one
raw data lake bucket, all partitioned by a `sport` key rather than
duplicated per sport. That means this wiring is identical regardless of
which sport instantiates it; only the env vars' actual values differ per
deployment, not the code.
"""
import os

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


class PipelineStorage:
    """Reads RAW_BUCKET_NAME/ENTITIES_TABLE_NAME/EVENTS_TABLE_NAME/
    PLAYER_GAME_STATS_TABLE_NAME/AWS_REGION from the environment. Same
    variable names every sport's task definition sets (see
    Terraform/ecs-task-nfl-backfill.tf for the pattern)."""

    def __init__(self):
        self.raw_bucket = _require_env("RAW_BUCKET_NAME")
        region = _require_env("AWS_REGION")

        self._raw_data_lake = S3Manager(self.raw_bucket, region=region)
        self._entities_table = DynamoDBTable(_require_env("ENTITIES_TABLE_NAME"), region=region)
        self._events_table = DynamoDBTable(_require_env("EVENTS_TABLE_NAME"), region=region)
        self._player_game_stats_table = DynamoDBTable(_require_env("PLAYER_GAME_STATS_TABLE_NAME"), region=region)

    def raw_object_exists(self, key: str) -> bool:
        return self._raw_data_lake.object_exists(key)

    def put_raw_json(self, key: str, payload: dict) -> None:
        self._raw_data_lake.put_json(key, payload)

    def upsert_entity(self, item: dict) -> None:
        self._entities_table.put_item(item)

    def upsert_event(self, item: dict) -> None:
        self._events_table.put_item(item)

    def write_player_game_stats(self, items: list[dict]) -> None:
        self._player_game_stats_table.batch_write(items, key_names=["event_key", "player_key"])
