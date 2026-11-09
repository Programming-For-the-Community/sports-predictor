"""
Shared S3 + DynamoDB wiring for a sport's ingest/backfill pipeline.
"""
import os

from boto3.dynamodb.conditions import Attr, Key

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.normalize.odds import LOCKED_AT, PREGAME_ODDS
from library.schema.keys import entity_key, entity_team_key


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def _lacks_conference(item: dict) -> bool:
    metadata = item.get("metadata")
    return metadata is not None and metadata.get("conference") is None


def _with_conference(item: dict, conference: str | None) -> dict:
    if conference is None or not _lacks_conference(item):
        return item
    return {**item, "metadata": {**item["metadata"], "conference": conference}}


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

    def upsert_entities(self, items: list[dict]) -> None:
        """Writes items in batches. A conference stamped by another writer
        (NCAA MBB schedule-sync) stays when a rewrite carries none."""
        missing = [item["entity_key"] for item in items if _lacks_conference(item)]
        stored = {
            existing["entity_key"]: conference
            for existing in self._entities_table.batch_get_items([{"entity_key": key} for key in dict.fromkeys(missing)])
            if (conference := (existing.get("metadata") or {}).get("conference")) is not None
        }
        self._entities_table.batch_write(
            [_with_conference(item, stored.get(item["entity_key"])) for item in items], key_names=["entity_key"],
        )

    def add_missing_entities(self, items: list[dict]) -> int:
        """Writes only the items whose entity isn't on file yet; returns how many."""
        keys = list(dict.fromkeys(item["entity_key"] for item in items))
        stored = self._entities_table.batch_get_items([{"entity_key": key} for key in keys])
        on_file = {existing["entity_key"] for existing in stored}
        missing = list({item["entity_key"]: item for item in items if item["entity_key"] not in on_file}.values())
        self._entities_table.batch_write(missing, key_names=["entity_key"])
        return len(missing)

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
        # The line on file stays when this rewrite has none (ESPN drops them
        # once a game starts) or when it was locked at kickoff.
        if item.get("event_type") == "head_to_head":
            existing = self._events_table.get_item({"event_key": item["event_key"]})
            kept = (existing or {}).get(PREGAME_ODDS)
            if kept is not None and (PREGAME_ODDS not in item or LOCKED_AT in kept):
                item = {**item, PREGAME_ODDS: kept}
        self._events_table.put_item(item)

    def set_event_odds(self, event_key: str, odds: dict) -> bool:
        """Saves an event's betting line without touching anything else on
        it. False when the event isn't on file yet or its line is locked."""
        return self._events_table.set_attribute_if_exists(
            {"event_key": event_key}, PREGAME_ODDS, odds, condition=Attr(f"{PREGAME_ODDS}.{LOCKED_AT}").not_exists(),
        )

    def get_events_on_date(self, sport: str, status: str, event_date: str) -> list[dict]:
        """A sport's events at `status` on one event_date, via the
        sport-status-index GSI (range key event_date)."""
        condition = Key("sport_status").eq(f"{sport}#{status}") & Key("event_date").eq(event_date)
        return self._events_table.query(condition, index_name="sport-status-index")

    def get_events_by_status(self, sport: str, status: str) -> list[dict]:
        """Every event for a sport currently at `status`, via the
        sport-status-index GSI."""
        return self._events_table.query(Key("sport_status").eq(f"{sport}#{status}"), index_name="sport-status-index")

    def get_event(self, event_key: str) -> dict | None:
        """One event by its own key, via a direct GetItem. Added for
        aws-lambdas/f1/normalize/handler.py's own schedule-stub write
        (library/normalize/f1.py's schedule_payload_to_scheduled_events)
        -- needs to check an event's already-stored status before
        upserting a "scheduled" stub over it, so a completed race never
        gets clobbered back to a resultless placeholder by the next
        day's own full-calendar schedule re-fetch. Same shape as
        library.storage.feature_storage.FeatureStorage's own get_event,
        just on the write-side pipeline storage class instead."""
        return self._events_table.get_item({"event_key": event_key})

    def get_entity(self, sport: str, entity_id: str, entity_type: str) -> dict | None:
        """One entity by id, via a direct GetItem. entity_type ("team" or
        "player") is required to build the key."""
        return self._entities_table.get_item({"entity_key": entity_key(sport, entity_id, entity_type)})

    def get_team_entities(self, sport: str, team_id: str) -> list[dict]:
        """Every player entity currently on file as rostered to team_id,
        via the entities table's team-index GSI -- same query
        library.storage.feature_storage.FeatureStorage's own
        get_team_entities makes read-side for NFL's live_features.py, just
        exposed here on the write-side storage class too. Used by a
        sport's own roster ingestion to detect a player who's dropped off
        a freshly-fetched roster entirely (see e.g.
        aws-lambdas/ncaafb/normalize/handler.py's own
        _clear_departed_players) -- upserting only ever adds/refreshes
        players actually present in a new roster payload, never removes
        one who's disappeared from it, so a departed player otherwise
        keeps their last-confirmed team_id forever."""
        return self._entities_table.query(Key("team_key").eq(entity_team_key(sport, team_id)), index_name="team-index")

    def write_player_game_stats(self, items: list[dict]) -> None:
        self._player_game_stats_table.batch_write(items, key_names=["event_key", "player_key"])

    def write_team_game_stats(self, items: list[dict]) -> None:
        self._team_game_stats_table.batch_write(items, key_names=["event_key", "team_key"])
