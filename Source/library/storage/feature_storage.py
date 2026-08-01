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
        self._team_game_stats_table = DynamoDBTable(_require_env("TEAM_GAME_STATS_TABLE_NAME"), region=region)

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

        Intended for one-team-at-a-time lookups (e.g. a future inference
        Lambda building a live feature vector for one upcoming matchup).
        Batch feature engineering over the whole history should call
        get_all_events() once instead -- calling this per team would scan
        the table once per team rather than once total.
        """
        events = self.get_all_events(sport)
        team_events = [
            event
            for event in events
            if any(p.get("entity_id") == entity_id for p in event.get("participants", []))
            and (before_date is None or event.get("event_date", "") < before_date)
        ]
        return team_events[:limit] if limit is not None else team_events

    def get_all_events(self, sport: str, status: str = "completed") -> list[dict]:
        """Every event for a sport, most recent first. Meant for batch jobs
        that need the whole history at once (e.g. Elo rating computation,
        which has to walk every team's games in chronological order, not
        just one team's) -- one scan instead of one Query per team.
        """
        items = self._events_table.scan()
        events = [
            item for item in items if item.get("sport") == sport and item.get("status") == status
        ]
        events.sort(key=lambda item: item.get("event_date", ""), reverse=True)
        return events

    def get_all_player_game_stats(self) -> list[dict]:
        """Every player_game_stats row, unsorted. Meant for batch jobs that
        need to group by player in memory rather than issue one
        entity-history Query per player (there can be thousands of
        distinct players across a full history).

        No sport filter -- player_game_stats rows don't carry a `sport`
        attribute directly (see design/DATA_SCHEMA.md), and only NFL data
        exists today. Once a second sport's data lands, filter by parsing
        the sport out of event_key's SPORT#<sport>#EVENT#... prefix.
        """
        return self._player_game_stats_table.scan()

    def get_all_team_game_stats(self) -> list[dict]:
        """Every team_game_stats row, unsorted. Same batch-job rationale
        as get_all_player_game_stats -- one scan instead of a per-team
        Query, and no sport filter since only NFL data exists today."""
        return self._team_game_stats_table.scan()
