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

from boto3.dynamodb.conditions import Attr, Key

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.schema.keys import entity_key


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
        self._team_game_stats_table = DynamoDBTable(_require_env("TEAM_GAME_STATS_TABLE_NAME"), region=region)

    def raw_object_exists(self, key: str) -> bool:
        return self._raw_data_lake.object_exists(key)

    def put_raw_json(self, key: str, payload: dict) -> None:
        self._raw_data_lake.put_json(key, payload)

    def upsert_entity(self, item: dict) -> None:
        self._entities_table.put_item(item)

    def upsert_player_entity(self, item: dict) -> None:
        """Same as upsert_entity, but only overwrites metadata.team_id if
        item's own metadata.team_id_as_of (see boxscore_to_player_game_stats)
        is the same age or newer than what's already stored. Silently
        skips the write otherwise -- no retry, nothing else for the
        caller to do."""
        as_of = item["metadata"]["team_id_as_of"]
        condition = Attr("metadata.team_id_as_of").not_exists() | Attr("metadata.team_id_as_of").lte(as_of)
        self._entities_table.put_item(item, condition_expression=condition)

    def upsert_event(self, item: dict) -> None:
        self._events_table.put_item(item)

    def get_events_by_status(self, sport: str, status: str) -> list[dict]:
        """Every event for a sport currently at `status` -- same status-
        index GSI pattern as FeatureStorage.get_all_events (Terraform/
        dynamodb-events.tf). Needed on the write side too (not just
        FeatureStorage's read-only copy) for normalize's own stale-event
        cleanup -- see aws-lambdas/ncaafb/normalize/handler.py's
        _cancel_stale_scheduled_events."""
        items = self._events_table.query(Key("status").eq(status), index_name="status-index")
        return [item for item in items if item.get("sport") == sport]

    def get_entity(self, sport: str, entity_id: str) -> dict | None:
        """One entity (team or player) by id -- a direct GetItem. Read-side
        counterpart to upsert_entity/upsert_player_entity, needed by
        normalize itself (not just downstream feature engineering/serving,
        which already has this same lookup via FeatureStorage.get_entity)
        whenever a write needs to see the entity it's about to overwrite
        first -- see aws-lambdas/ncaafb/normalize/handler.py's
        _preserve_roster_position for the motivating case."""
        return self._entities_table.get_item({"entity_key": entity_key(sport, entity_id)})

    def write_player_game_stats(self, items: list[dict]) -> None:
        self._player_game_stats_table.batch_write(items, key_names=["event_key", "player_key"])

    def write_team_game_stats(self, items: list[dict]) -> None:
        self._team_game_stats_table.batch_write(items, key_names=["event_key", "team_key"])
