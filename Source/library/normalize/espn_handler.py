"""
The normalize Lambda body shared by every ESPN-sourced head-to-head sport
(NFL/NBA/NCAA MBB): teams, scoreboard, roster and box-score payloads from
the raw bucket, written through the sport's own PipelineStorage. Sports
differ only in how their box scores' compound stat keys split
("fieldGoalsMade-fieldGoalsAttempted" etc.).
"""
import logging
from collections.abc import Callable

from library.normalize import dispatch as dispatch_common
from library.normalize.espn import (
    boxscore_to_player_game_stats,
    boxscore_to_team_game_stats,
    roster_to_player_entities,
    scoreboard_event_to_event_item,
    team_to_entity,
)

CompoundKeySplits = dict[str, tuple[str, str]]


class EspnNormalizer:
    def __init__(
        self, sport: str, get_storage: Callable[[], object], logger: logging.Logger, *,
        player_compound_key_splits: CompoundKeySplits, team_compound_key_splits: CompoundKeySplits,
    ) -> None:
        """`get_storage` is the handler's own lazy PipelineStorage getter."""
        self._sport = sport
        self._get_storage = get_storage
        self._logger = logger
        self._player_splits = player_compound_key_splits
        self._team_splits = team_compound_key_splits

    def process_teams(self, payload: dict, key: str) -> None:
        storage = self._get_storage()
        league = payload["sports"][0]["leagues"][0]
        for team_entry in league["teams"]:
            storage.upsert_entity(team_to_entity(team_entry["team"], self._sport))
        self._logger.info("Upserted %d team entities from %s", len(league["teams"]), key)

    def process_scoreboard(self, payload: dict, key: str) -> None:
        storage = self._get_storage()
        events = payload.get("events", [])
        for event in events:
            storage.upsert_event(scoreboard_event_to_event_item(event, self._sport))
        self._logger.info("Upserted %d events from %s", len(events), key)

    def clear_departed_players(self, storage, team_id: str, entities: list[dict], as_of_date: str) -> int:
        """Clears metadata.team_id (and drops the entity out of the
        team-index GSI, by omitting team_key from the rewritten item) for
        any player on file for team_id that this fresh roster snapshot no
        longer lists. ESPN's roster response only says who IS on the team
        now, so without this a released or retired player keeps their
        last team_id forever -- still showing up as a current-team stat
        leader.

        An empty `entities` list is left alone (returns 0 without
        querying): a transient ESPN gap for one team is far likelier than
        every player leaving at once, and treating it as "everyone left"
        would wipe the team's roster attribution over one bad fetch."""
        if not entities:
            return 0
        present_ids = {entity["entity_id"] for entity in entities}
        cleared = 0
        for on_file in storage.get_team_entities(self._sport, team_id):
            entity_id = on_file.get("entity_id")
            if entity_id is None or entity_id in present_ids:
                continue
            metadata = dict(on_file.get("metadata") or {})
            metadata.pop("team_id", None)
            metadata["team_id_as_of"] = as_of_date
            cleared_entity = {k: v for k, v in on_file.items() if k != "team_key"}
            cleared_entity["metadata"] = metadata
            if storage.upsert_player_entity(cleared_entity):
                cleared += 1
        return cleared

    def process_roster(self, payload: dict, key: str) -> None:
        storage = self._get_storage()
        entities = roster_to_player_entities(payload, self._sport)
        for entity in entities:
            storage.upsert_player_entity(entity)
        team_id = str(payload["team"]["id"])
        as_of_date = payload["timestamp"][:10]
        cleared = self.clear_departed_players(storage, team_id, entities, as_of_date)
        self._logger.info(
            "Upserted %d player entities (%d cleared as no longer rostered) from %s", len(entities), cleared, key,
        )

    def process_boxscore(self, payload: dict, key: str) -> None:
        storage = self._get_storage()
        stats_items, player_entities = boxscore_to_player_game_stats(payload, self._sport, self._player_splits)
        for entity in player_entities:
            storage.upsert_player_entity(entity)
        storage.write_player_game_stats(stats_items)
        self._logger.info(
            "Wrote %d player stat lines and %d player entities from %s", len(stats_items), len(player_entities), key,
        )

        team_stats_items = boxscore_to_team_game_stats(payload, self._sport, self._team_splits)
        storage.write_team_game_stats(team_stats_items)
        self._logger.info("Wrote %d team stat lines from %s", len(team_stats_items), key)

    def dispatch(self, s3, bucket: str, key: str) -> None:
        dispatch_common.dispatch(
            s3, bucket, key, self._logger,
            process_teams=self.process_teams, process_scoreboard=self.process_scoreboard,
            process_boxscore=self.process_boxscore, process_roster=self.process_roster,
        )
