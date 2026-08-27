"""
Shared S3 + DynamoDB wiring for a sport's ingest/backfill pipeline.
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
    PLAYER_GAME_STATS_TABLE_NAME/TEAM_GAME_STATS_TABLE_NAME/AWS_REGION
    from the environment."""

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

    def get_raw_json(self, key: str) -> dict:
        """Reads back an already-cached raw object (see
        data-backfills/pga/backfill.py's process_tournament -- a re-run
        that finds the raw JSON already cached re-processes it from here
        instead of re-fetching from ESPN, so a normalizer/dispatch code
        change picked up between runs still applies to already-cached
        tournaments)."""
        return self._raw_data_lake.get_json(key)

    def upsert_entity(self, item: dict) -> None:
        self._entities_table.put_item(item)

    def upsert_player_entity(self, item: dict) -> bool:
        """Same as upsert_entity, but only overwrites metadata.team_id if
        item's own metadata.team_id_as_of is the same age or newer than
        what's already stored. Returns whether the write happened."""
        as_of = item["metadata"]["team_id_as_of"]
        condition = Attr("metadata.team_id_as_of").not_exists() | Attr("metadata.team_id_as_of").lte(as_of)
        return self._entities_table.put_item(item, condition_expression=condition)

    def upsert_event(self, item: dict) -> None:
        # sport_status backs the sport-status-index GSI (dynamodb-events.tf)
        # -- lets get_events_by_status/FeatureStorage.get_all_events Query
        # one sport directly instead of pulling every sport at that status
        # off status-index and discarding most of it in Python.
        if "sport" in item and "status" in item:
            item = {**item, "sport_status": f"{item['sport']}#{item['status']}"}
        self._events_table.put_item(item)

    def get_events_by_status(self, sport: str, status: str) -> list[dict]:
        """Every event for a sport currently at `status`, via the
        sport-status-index GSI."""
        return self._events_table.query(Key("sport_status").eq(f"{sport}#{status}"), index_name="sport-status-index")

    def get_entity(self, sport: str, entity_id: str, entity_type: str) -> dict | None:
        """One entity by id, via a direct GetItem. entity_type ("team" or
        "player") is required to build the key."""
        return self._entities_table.get_item({"entity_key": entity_key(sport, entity_id, entity_type)})

    def write_player_game_stats(self, items: list[dict]) -> None:
        self._player_game_stats_table.batch_write(items, key_names=["event_key", "player_key"])

    def write_team_game_stats(self, items: list[dict]) -> None:
        self._team_game_stats_table.batch_write(items, key_names=["event_key", "team_key"])
