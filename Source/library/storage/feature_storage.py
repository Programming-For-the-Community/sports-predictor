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
from library.schema.keys import entity_key, entity_team_key


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


class FeatureStorage:
    """Reads EVENTS_TABLE_NAME/PLAYER_GAME_STATS_TABLE_NAME from the
    environment -- same variable names PipelineStorage uses, so a
    feature-engineering task's env vars look identical to an
    ingest/backfill task's (see Terraform/ecs-task-nfl-backfill.tf).

    ENTITIES_TABLE_NAME is only required by get_entity below -- batch
    feature engineering never reads the entities table, only the live
    inference Lambda does (see Source/aws-lambdas/nfl/predict), so this
    stays required rather than optional to fail loudly if a caller that
    needs it forgot to set it, not silently return None forever.
    """

    def __init__(self):
        region = os.environ.get("AWS_REGION")
        self._entities_table = DynamoDBTable(_require_env("ENTITIES_TABLE_NAME"), region=region)
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
        self, sport: str, entity_id: str, before_date: str | None = None, limit: int | None = None,
        events: list[dict] | None = None,
    ) -> list[dict]:
        """A team's completed games, most recent first. No GSI on `events`
        for entity_id yet -- design/DATA_SCHEMA.md defers that until it's
        actually painful, and NFL's ~2,720 total games is cheap enough to
        scan and filter in Python instead.

        Intended for one-team-at-a-time lookups (e.g. a live inference
        Lambda building a feature vector for one upcoming matchup). A
        SINGLE such request can easily call this many times over (once per
        team per rolling window, once per presumptive-leader candidate
        search, ...) -- pass an already-fetched get_all_events(sport)
        result as `events` to filter it in memory instead of re-querying
        the whole sport's history again on every call. This is exactly
        the redundant-requery pattern that made NCAAFB's live predict
        route slow (10 full-history queries in one request, see
        Source/aws-lambdas/ncaafb/predict/live_features.py's own
        docstring) -- omitted (None), this still re-fetches every time,
        so a caller that doesn't thread `events` through just gets the
        old (correct, just slower) behavior.

        Batch feature engineering over the whole history should still
        call get_all_events() once and iterate in Python directly,
        rather than calling this per team at all -- see this docstring's
        own history for why (each call, even with `events` given, still
        does an O(n) scan of the full list).
        """
        all_events = events if events is not None else self.get_all_events(sport)
        team_events = [
            event
            for event in all_events
            if any(p.get("entity_id") == entity_id for p in event.get("participants", []))
            and (before_date is None or event.get("event_date", "") < before_date)
        ]
        return team_events[:limit] if limit is not None else team_events

    def get_all_events(self, sport: str, status: str = "completed") -> list[dict]:
        """Every event for a sport, most recent first. Meant for batch jobs
        that need the whole history at once (e.g. Elo rating computation,
        which has to walk every team's games in chronological order, not
        just one team's) -- one Query instead of one Query per team.

        Queries the status-index GSI (Terraform/dynamodb-events.tf) rather
        than scanning the whole table. scan_index_forward=False means the
        index already returns results most-recent-first (its range key is
        event_date), so no separate Python sort is needed. sport is still
        filtered in Python since the index is keyed on status alone --
        cheap once status has already narrowed the result set via the
        index, unlike filtering sport out of a full table scan.
        """
        items = self._events_table.query(
            Key("status").eq(status), index_name="status-index", scan_index_forward=False,
        )
        return [item for item in items if item.get("sport") == sport]

    def get_all_player_game_stats(self, sport: str) -> list[dict]:
        """Every player_game_stats row for one sport, unsorted. Meant for
        batch jobs that need to group by player in memory rather than
        issue one entity-history Query per player (there can be thousands
        of distinct players across a full history).

        Queries the sport-index GSI (Terraform/dynamodb-player-game-stats.tf)
        rather than scanning the whole table and filtering by event_key
        prefix in Python -- that Scan-based version read every OTHER
        sport's rows too once a second sport lands in this shared table.
        `sport` is written directly onto every row now (library.normalize.
        espn.boxscore_to_player_game_stats); older rows need
        Source/migrations/backfill_sport_attribute.py run once first, or
        this Query simply won't see them (a GSI is sparse -- a row missing
        any of its key attributes isn't in the index at all)."""
        return self._player_game_stats_table.query(Key("sport").eq(sport), index_name="sport-index")

    def get_all_team_game_stats(self, sport: str) -> list[dict]:
        """Every team_game_stats row for one sport, unsorted. Same
        sport-index GSI rationale as get_all_player_game_stats."""
        return self._team_game_stats_table.query(Key("sport").eq(sport), index_name="sport-index")

    def get_team_game_stats_for_team(
        self, sport: str, entity_id: str, before_date: str | None = None, limit: int | None = None,
        team_game_stats: list[dict] | None = None,
    ) -> list[dict]:
        """A team's own completed team_game_stats rows, most recent first.
        Same one-team-at-a-time lookup shape as get_team_events -- no
        dedicated per-team GSI, so this filters get_all_team_game_stats'
        sport-scoped result down to one team in Python, same as
        get_team_events does against get_all_events. `team_game_stats`
        is the same optional already-fetched-result pass-through
        get_team_events' own `events` parameter is, for the same reason
        -- see that parameter's own docstring."""
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
        player_game_stats' own partition key (event_key is the hash key --
        see Terraform/dynamodb-player-game-stats.tf) rather than a scan.
        Meant for one-event-at-a-time lookups, e.g. identifying last
        game's starting QB/lead rusher/lead receiver for a live inference
        request -- design/DATA_SCHEMA.md calls this exact access pattern
        out: 'querying "all player stat lines for this game" is a single
        partition query.'"""
        return self._player_game_stats_table.query(Key("event_key").eq(event_key))

    def get_event(self, event_key: str) -> dict | None:
        """One event by its own key -- a direct GetItem, for a live
        inference request about one specific (usually not-yet-played)
        game, unlike get_all_events/get_team_events which return many."""
        return self._events_table.get_item({"event_key": event_key})

    def get_entity(self, sport: str, entity_id: str, entity_type: str) -> dict | None:
        """One entity by id -- a direct GetItem. entity_type ("team" or
        "player") is required, not inferred -- see entity_key's own
        docstring for why a type-less key can't disambiguate two different
        entities. Only get_entity needs ENTITIES_TABLE_NAME; every other
        method here reads events/player_game_stats/team_game_stats, which
        batch feature engineering never needs the entities table for."""
        return self._entities_table.get_item({"entity_key": entity_key(sport, entity_id, entity_type)})

    def get_team_entities(self, sport: str, team_id: str) -> list[dict]:
        """Every player currently rostered to team_id (team_key, kept
        fresh by roster sync's daily upsert -- see normalize/espn.py) --
        the roster as it stands today, not who last recorded a stat line
        for this team. Used by live_features.py's presumptive-leader
        candidate selection instead of box-score history, so a just-traded
        player shows up under their new team immediately rather than only
        after they've played a game for it."""
        return self._entities_table.query(Key("team_key").eq(entity_team_key(sport, team_id)), index_name="team-index")
