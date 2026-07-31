"""
Read-only DynamoDB access for feature engineering. Deliberately separate
from PipelineStorage (pipeline_storage.py), which is write-only and scoped
to the ingest/backfill pipeline -- feature engineering is a different
consumer of the same tables, reading history back out rather than writing
new rows in. Splitting read and write access into separate classes means
neither one grows methods the other doesn't need.

DynamoDB and S3 concerns also stay in separate classes here, same as
PipelineStorage: this wraps only DynamoDBTable instances. Feature
engineering doesn't touch the raw data lake bucket at all -- it only ever
reads what normalize.py already wrote to DynamoDB.
"""
import os

from boto3.dynamodb.conditions import Key

from library.aws.dynamodb_table import DynamoDBTable


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


class FeatureStorage:
    """Reads EVENTS_TABLE_NAME/PLAYER_GAME_STATS_TABLE_NAME from the
    environment -- same variable names PipelineStorage uses, so a
    feature-engineering task's env vars look identical to an
    ingest/backfill task's (see Terraform/ecs-task-nfl-backfill.tf)."""

    def __init__(self):
        region = os.environ.get("AWS_REGION")
        self._events_table = DynamoDBTable(_require_env("EVENTS_TABLE_NAME"), region=region)
        self._player_game_stats_table = DynamoDBTable(_require_env("PLAYER_GAME_STATS_TABLE_NAME"), region=region)

    def get_player_game_stats(
        self, entity_id: str, before_date: str | None = None, limit: int | None = None
    ) -> list[dict]:
        """A player's game log, most recent first, via the entity-history
        GSI (see Terraform/dynamodb-player-game-stats.tf). Only ever
        contains completed games -- normalize.py only writes a
        player_game_stats row once ingest has a final box score.

        before_date is exclusive, ISO 8601 ("2025-09-28") -- pass the
        upcoming game's date to get every prior game without including one
        not yet played.
        """
        condition = Key("entity_id").eq(entity_id)
        if before_date is not None:
            condition = condition & Key("event_date").lt(before_date)
        return self._player_game_stats_table.query(
            condition, index_name="entity-history", scan_index_forward=False, limit=limit,
        )

    def get_team_events(
        self, sport: str, entity_id: str, before_date: str | None = None, limit: int | None = None
    ) -> list[dict]:
        """A team's completed games, most recent first. No GSI on `events`
        for entity_id yet -- design/DATA_SCHEMA.md defers that until it's
        actually painful, and NFL's ~2,720 total games is cheap enough to
        scan and filter in Python instead.
        """
        items = self._events_table.scan()
        team_events = [
            item
            for item in items
            if item.get("sport") == sport
            and item.get("status") == "completed"
            and any(p.get("entity_id") == entity_id for p in item.get("participants", []))
            and (before_date is None or item.get("event_date", "") < before_date)
        ]
        team_events.sort(key=lambda item: item.get("event_date", ""), reverse=True)
        return team_events[:limit] if limit is not None else team_events
