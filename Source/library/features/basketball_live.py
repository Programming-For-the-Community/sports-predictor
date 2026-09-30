"""
Live (serving-time) feature rows for the basketball sports -- NBA and NCAA
MBB build their event, player-prop and leader-candidate rows the same way,
differing only in their own feature builders (library.features.nba /
ncaambb), their box-score candidate search, and how far back the event
row reads team box scores.
"""
from collections.abc import Callable
from datetime import date, timedelta

from library.features.common import compute_elo_ratings
from library.features.live_orchestration import (
    EventNotFoundError,
    home_away_ids,
    live_elo_ratings,
    team_previous_event_date,
    top_n_by_recent_volume,
)
from library.schema.keys import player_key

LEADER_VOLUME_STATS = {"scoring": "points", "rebounding": "rebounds", "assists": "assists"}
LEADER_CANDIDATE_LIMITS = {"scoring": 5, "rebounding": 5, "assists": 5}


class BasketballLiveFeatures:
    def __init__(
        self, *, build_event_features: Callable, build_player_features: Callable, candidate_ids: Callable,
        default_window: int, team_game_stats_lookback_days: int | None,
    ) -> None:
        """`candidate_ids(storage, sport, team_id, before_date, current_season,
        stat_key, events)` is the sport's own box-score candidate search.
        `team_game_stats_lookback_days` of None reads every team box score."""
        self._build_event_features = build_event_features
        self._build_player_features = build_player_features
        self._candidate_ids = candidate_ids
        self._default_window = default_window
        self._lookback_days = team_game_stats_lookback_days

    def _event(self, storage, event_key: str) -> dict:
        event = storage.get_event(event_key)
        if event is None:
            raise EventNotFoundError(f"No event found for {event_key}")
        return event

    def build_player_feature_row(
        self, storage, sport: str, event: dict, home_id: str, away_id: str, entity_id: str, team_id: str,
        prior_games: list[dict], window: int, current_ratings: dict | None = None, events: list[dict] | None = None,
    ) -> dict:
        player_game = {
            "event_key": event["event_key"],
            "player_key": player_key(sport, entity_id),
            "entity_id": entity_id,
            "team_id": team_id,
            "event_date": event["event_date"],
            "stat_line": {},
        }
        return self._build_player_features(
            player_game, prior_games, event,
            live_elo_ratings(storage, sport, event, home_id, away_id, current_ratings, events),
            team_previous_event_date(storage, sport, team_id, event["event_date"], events),
            window,
        )

    def build_live_event_features(
        self, storage, sport: str, event_key: str, window: int | None = None, events: list[dict] | None = None,
    ) -> dict:
        """One event-level feature row for event_key, in the shape the
        win-probability and score models were trained on."""
        window = self._default_window if window is None else window
        event = self._event(storage, event_key)
        home_id, away_id = home_away_ids(event)
        before_date = event["event_date"]
        events = events if events is not None else storage.get_all_events(sport)
        if self._lookback_days is None:
            team_game_stats = storage.get_all_team_game_stats(sport)
        else:
            since_date = (date.fromisoformat(before_date) - timedelta(days=self._lookback_days)).isoformat()
            team_game_stats = storage.get_all_team_game_stats(sport, since_date=since_date)

        def team_rows(team_id: str) -> tuple[list[dict], list[dict]]:
            return (
                storage.get_team_events(sport, team_id, before_date=before_date, limit=window, events=events),
                storage.get_team_game_stats_for_team(
                    sport, team_id, before_date=before_date, limit=window, team_game_stats=team_game_stats,
                ),
            )

        home_events, home_box = team_rows(home_id)
        away_events, away_box = team_rows(away_id)
        return self._build_event_features(
            event, live_elo_ratings(storage, sport, event, home_id, away_id, events=events),
            home_events, away_events, window,
            home_team_box_stats=home_box, away_team_box_stats=away_box,
        )

    def build_live_player_features(
        self, storage, sport: str, event_key: str, entity_id: str, window: int | None = None,
        current_ratings: dict | None = None, events: list[dict] | None = None,
    ) -> dict:
        """One player-prop feature row for entity_id in event_key. team_id
        comes from the player's own entity record, not their last game."""
        window = self._default_window if window is None else window
        event = self._event(storage, event_key)
        home_id, away_id = home_away_ids(event)

        entity = storage.get_entity(sport, entity_id, "player")
        if entity is None:
            raise EventNotFoundError(f"No entity found for {entity_id}")
        team_id = (entity.get("metadata") or {}).get("team_id")

        prior_games = storage.get_player_game_stats(entity_id, before_date=event["event_date"], limit=window)
        return self.build_player_feature_row(
            storage, sport, event, home_id, away_id, entity_id, team_id, prior_games, window, current_ratings, events,
        )

    def build_live_event_leader_candidates(
        self, storage, sport: str, event_key: str, window: int | None = None, events: list[dict] | None = None,
    ) -> dict:
        """One feature row per candidate likely to lead each team in
        scoring/rebounding/assists, grouped {"home": {"scoring": [row, ...],
        ...}, "away": {...}}. Loads no model -- scoring the rows is the
        caller's job."""
        window = self._default_window if window is None else window
        event = self._event(storage, event_key)
        home_id, away_id = home_away_ids(event)
        before_date = event["event_date"]
        current_season = event.get("season")
        events = events if events is not None else storage.get_all_events(sport)
        _, current_ratings = compute_elo_ratings(events, as_of_season=current_season)

        def team_candidates(team_id: str) -> dict:
            result = {}
            for category, stat_key in LEADER_VOLUME_STATS.items():
                candidate_ids = self._candidate_ids(storage, sport, team_id, before_date, current_season, stat_key, events)
                histories = top_n_by_recent_volume(
                    storage, candidate_ids, stat_key, before_date, window, LEADER_CANDIDATE_LIMITS[category],
                )
                result[category] = [
                    self.build_player_feature_row(
                        storage, sport, event, home_id, away_id, entity_id, team_id, prior_games, window,
                        current_ratings, events,
                    )
                    for entity_id, prior_games in histories.items()
                ]
            return result

        return {"home": team_candidates(home_id), "away": team_candidates(away_id)}
