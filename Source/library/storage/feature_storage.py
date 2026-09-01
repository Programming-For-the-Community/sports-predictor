"""
Read-only DynamoDB access for feature engineering. Separate from
PipelineStorage (pipeline_storage.py), which is write-only.
"""
import os

from boto3.dynamodb.conditions import Key

from library.aws.dynamodb_table import DynamoDBTable
from library.schema.keys import entity_key, entity_team_key


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


class FeatureStorage:
    """Reads ENTITIES_TABLE_NAME/EVENTS_TABLE_NAME/PLAYER_GAME_STATS_TABLE_NAME/
    TEAM_GAME_STATS_TABLE_NAME from the environment."""

    def __init__(self):
        region = os.environ.get("AWS_REGION")
        self._entities_table = DynamoDBTable(_require_env("ENTITIES_TABLE_NAME"), region=region)
        self._events_table = DynamoDBTable(_require_env("EVENTS_TABLE_NAME"), region=region)
        self._player_game_stats_table = DynamoDBTable(_require_env("PLAYER_GAME_STATS_TABLE_NAME"), region=region)
        self._team_game_stats_table = DynamoDBTable(_require_env("TEAM_GAME_STATS_TABLE_NAME"), region=region)

    def get_player_game_stats(
        self, entity_id: str, before_date: str | None = None, limit: int | None = None
    ) -> list[dict]:
        """A player's completed-game log, most recent first, via the
        entity-history GSI. before_date is exclusive, ISO 8601."""
        condition = Key("entity_id").eq(entity_id)
        if before_date is not None:
            condition = condition & Key("event_date").lt(before_date)
        return self._player_game_stats_table.query(
            condition, index_name="entity-history", scan_index_forward=False, limit=limit,
        )

    def get_team_events(
        self, sport: str, entity_id: str, before_date: str | None = None, limit: int | None = None,
        events: list[dict] | None = None,
    ) -> list[dict]:
        """A team's completed games, most recent first. Filters an
        already-fetched get_all_events(sport) result if `events` is
        passed; otherwise re-fetches the whole sport's history."""
        all_events = events if events is not None else self.get_all_events(sport)
        team_events = [
            event
            for event in all_events
            if any(p.get("entity_id") == entity_id for p in event.get("participants", []))
            and (before_date is None or event.get("event_date", "") < before_date)
        ]
        return team_events[:limit] if limit is not None else team_events

    def get_all_events(
        self, sport: str, status: str = "completed", scan_index_forward: bool = False, limit: int | None = None,
        since_date: str | None = None,
    ) -> list[dict]:
        """Every event for a sport, most recent first (or soonest first,
        scan_index_forward=True), via the sport-status-index GSI (range key
        is event_date, so no separate sort is needed) -- Queries the sport
        directly instead of pulling every sport at `status` off
        status-index and discarding the rest in Python. limit bounds the
        query to that many of the most-recent-or-soonest rows instead of
        the whole history -- a caller that only needs the most recent/
        soonest date's events (not full-season history, e.g. ELO) should
        pass this; sorted by event_date, so a coarse limit still reliably
        includes every event on that one date regardless of how long ago
        or far ahead it falls. since_date (inclusive, ISO 8601) bounds the
        query the same way get_all_team_game_stats' own since_date does --
        a training-set-building caller that wants a rolling lookback
        window (not full history) should pass this instead of limit."""
        condition = Key("sport_status").eq(f"{sport}#{status}")
        if since_date is not None:
            condition = condition & Key("event_date").gte(since_date)
        return self._events_table.query(
            condition, index_name="sport-status-index", scan_index_forward=scan_index_forward, limit=limit,
        )

    def get_all_player_game_stats(self, sport: str, since_date: str | None = None) -> list[dict]:
        """Every player_game_stats row for one sport, unsorted, via the
        sport-index GSI. since_date (inclusive, ISO 8601) bounds the query
        the same way get_all_team_game_stats' own since_date does."""
        condition = Key("sport").eq(sport)
        if since_date is not None:
            condition = condition & Key("event_date").gte(since_date)
        return self._player_game_stats_table.query(condition, index_name="sport-index")

    def get_all_team_game_stats(self, sport: str, since_date: str | None = None) -> list[dict]:
        """Every team_game_stats row for one sport, unsorted, via the
        sport-index GSI. since_date (inclusive, ISO 8601) bounds the query
        to a recent window -- a caller that only needs each team's most
        recent few games (not full-season history) should pass this."""
        condition = Key("sport").eq(sport)
        if since_date is not None:
            condition = condition & Key("event_date").gte(since_date)
        return self._team_game_stats_table.query(condition, index_name="sport-index")

    def get_team_game_stats_for_team(
        self, sport: str, entity_id: str, before_date: str | None = None, limit: int | None = None,
        team_game_stats: list[dict] | None = None,
    ) -> list[dict]:
        """A team's own completed team_game_stats rows, most recent first.
        Filters an already-fetched get_all_team_game_stats' result if
        `team_game_stats` is passed."""
        rows = team_game_stats if team_game_stats is not None else self.get_all_team_game_stats(sport)
        team_rows = [
            row
            for row in rows
            if row.get("team_id") == entity_id and (before_date is None or row.get("event_date", "") < before_date)
        ]
        team_rows.sort(key=lambda row: row.get("event_date", ""), reverse=True)
        return team_rows[:limit] if limit is not None else team_rows

    def get_player_game_stats_for_event(self, event_key: str) -> list[dict]:
        """Every player's stat line for one game, via a direct Query on
        player_game_stats' partition key (event_key)."""
        return self._player_game_stats_table.query(Key("event_key").eq(event_key))

    def get_event(self, event_key: str) -> dict | None:
        """One event by its own key, via a direct GetItem."""
        return self._events_table.get_item({"event_key": event_key})

    def get_entity(self, sport: str, entity_id: str, entity_type: str) -> dict | None:
        """One entity by id, via a direct GetItem. entity_type ("team" or
        "player") is required to build the key."""
        return self._entities_table.get_item({"entity_key": entity_key(sport, entity_id, entity_type)})

    def get_entities(self, sport: str, entity_refs: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """Batched form of get_entity -- entity_refs is [(entity_id,
        entity_type), ...]. Returns {(entity_id, entity_type): entity},
        one BatchGetItem round trip per 100 refs instead of one GetItem
        per ref -- the fix behind library.serving.common's own
        enrich_participants(..., entity_cache=...) fast path (real
        production 504 serving PGA's completed-tournaments list: up to
        ~150 golfers x every historical tournament, sequential GetItems,
        real complaint 2026-09-01). A ref with no matching entity is
        simply absent from the result, same "no error on a miss" contract
        get_entity already has. Deduplicates entity_refs internally --
        the same (entity_id, entity_type) pair repeated across many
        events' participant lists is fetched once."""
        unique_refs = list(dict.fromkeys(entity_refs))
        if not unique_refs:
            return {}
        keys = [{"entity_key": entity_key(sport, entity_id, entity_type)} for entity_id, entity_type in unique_refs]
        items_by_key = {item["entity_key"]: item for item in self._entities_table.batch_get_items(keys)}
        return {
            (entity_id, entity_type): items_by_key[entity_key(sport, entity_id, entity_type)]
            for entity_id, entity_type in unique_refs
            if entity_key(sport, entity_id, entity_type) in items_by_key
        }

    def get_team_entities(self, sport: str, team_id: str) -> list[dict]:
        """Every player currently rostered to team_id, via the entities
        table's team-index GSI."""
        return self._entities_table.query(Key("team_key").eq(entity_team_key(sport, team_id)), index_name="team-index")
