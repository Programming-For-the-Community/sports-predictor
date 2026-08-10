"""
Unit tests for ncaafb/predict/live_features.py -- especially
_presumptive_leader, the season-crossing last-game-box-score leader
lookup with a still-on-team verification this module uses in place of
NFL's roster+depth-chart-driven selection (see that module's own
docstring for why). FeatureStorage is mocked throughout.

The ncaafb_predict module (and this directory's own sys.path entry for
predict/) is registered by conftest.py.
"""
from unittest.mock import MagicMock

import pytest

import live_features


def _event(event_key, event_date, home_id, away_id, season=2025, home_score=None, away_score=None):
    home_result = {"score": home_score, "won": home_score is not None and away_score is not None and home_score > away_score}
    away_result = {"score": away_score, "won": home_score is not None and away_score is not None and away_score > home_score}
    return {
        "event_key": event_key,
        "event_date": event_date,
        "season": season,
        "week": 5,
        "season_type": "regular",
        "venue_indoor": False,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }


def _player_game(entity_id, team_id, event_key, event_date, stat_line):
    return {
        "entity_id": entity_id, "team_id": team_id, "event_key": event_key,
        "event_date": event_date, "player_key": f"SPORT#NCAAFB#PLAYER#{entity_id}", "stat_line": stat_line,
    }


def _entity(team_id):
    return {"metadata": {"team_id": team_id}}


class TestStillOnTeam:
    def test_true_when_entity_team_matches(self):
        storage = MagicMock()
        storage.get_entity.return_value = _entity("61")
        assert live_features._still_on_team(storage, "ncaafb", "101", "61") is True

    def test_false_when_entity_team_differs(self):
        storage = MagicMock()
        storage.get_entity.return_value = _entity("52")
        assert live_features._still_on_team(storage, "ncaafb", "101", "61") is False

    def test_false_when_entity_missing(self):
        storage = MagicMock()
        storage.get_entity.return_value = None
        assert live_features._still_on_team(storage, "ncaafb", "101", "61") is False


class TestPresumptiveLeader:
    def test_finds_leader_from_most_recent_game(self):
        storage = MagicMock()
        game = _event("SPORT#NCAAFB#EVENT#900", "2025-10-04", "61", "52")
        storage.get_team_events.return_value = [game]
        storage.get_player_game_stats_for_event.return_value = [
            _player_game("101", "61", game["event_key"], "2025-10-04", {"passing_attempts": 30}),
        ]
        storage.get_entity.return_value = _entity("61")
        storage.get_player_game_stats.return_value = [{"entity_id": "101"}]

        result = live_features._presumptive_leader(storage, "ncaafb", "61", "2025-10-11", 2025, "passing", 5)

        assert result == ("101", [{"entity_id": "101"}])

    def test_skips_a_transferred_candidate_and_keeps_searching(self):
        storage = MagicMock()
        recent = _event("SPORT#NCAAFB#EVENT#900", "2025-10-04", "61", "52")
        older = _event("SPORT#NCAAFB#EVENT#800", "2025-09-27", "61", "70")
        storage.get_team_events.return_value = [recent, older]

        def _games_for_event(event_key):
            if event_key == recent["event_key"]:
                return [_player_game("101", "61", event_key, "2025-10-04", {"passing_attempts": 30})]
            return [_player_game("102", "61", event_key, "2025-09-27", {"passing_attempts": 25})]

        storage.get_player_game_stats_for_event.side_effect = _games_for_event
        # 101 (most recent game's leader) has since transferred to team 99;
        # 102 (the older game's leader) is still on 61.
        storage.get_entity.side_effect = lambda sport, entity_id: _entity("99") if entity_id == "101" else _entity("61")
        storage.get_player_game_stats.return_value = [{"entity_id": "102"}]

        result = live_features._presumptive_leader(storage, "ncaafb", "61", "2025-10-11", 2025, "passing", 5)

        assert result[0] == "102"

    def test_crosses_season_boundary_for_week_0_target(self):
        # No current-season (2026) games exist yet -- the walk should fall
        # through to last season's (2025) finale without any special-casing.
        storage = MagicMock()
        bowl_game = _event("SPORT#NCAAFB#EVENT#700", "2026-01-01", "61", "52", season=2025)
        storage.get_team_events.return_value = [bowl_game]
        storage.get_player_game_stats_for_event.return_value = [
            _player_game("101", "61", bowl_game["event_key"], "2026-01-01", {"passing_attempts": 28}),
        ]
        storage.get_entity.return_value = _entity("61")
        storage.get_player_game_stats.return_value = [{"entity_id": "101"}]

        result = live_features._presumptive_leader(storage, "ncaafb", "61", "2026-08-30", 2026, "passing", 5)

        assert result[0] == "101"

    def test_stops_at_the_season_lookback_bound(self):
        # Target season 2026, SEASON_LOOKBACK=1 -- only 2026/2025 games are
        # eligible. A 2024 game should never even be queried for its box
        # score once the walk reaches it.
        storage = MagicMock()
        too_old = _event("SPORT#NCAAFB#EVENT#600", "2024-11-01", "61", "52", season=2024)
        storage.get_team_events.return_value = [too_old]

        result = live_features._presumptive_leader(storage, "ncaafb", "61", "2026-08-30", 2026, "passing", 5)

        assert result is None
        storage.get_player_game_stats_for_event.assert_not_called()

    def test_returns_none_when_no_game_has_the_stat_at_all(self):
        storage = MagicMock()
        game = _event("SPORT#NCAAFB#EVENT#900", "2025-10-04", "61", "52")
        storage.get_team_events.return_value = [game]
        storage.get_player_game_stats_for_event.return_value = [
            _player_game("101", "61", game["event_key"], "2025-10-04", {"rushing_attempts": 10}),  # no passing_attempts
        ]

        result = live_features._presumptive_leader(storage, "ncaafb", "61", "2025-10-11", 2025, "passing", 5)

        assert result is None

    def test_returns_none_when_no_prior_games_exist(self):
        storage = MagicMock()
        storage.get_team_events.return_value = []

        result = live_features._presumptive_leader(storage, "ncaafb", "61", "2025-09-01", 2025, "passing", 5)

        assert result is None

    def test_filters_box_score_to_the_requested_team_only(self):
        storage = MagicMock()
        game = _event("SPORT#NCAAFB#EVENT#900", "2025-10-04", "61", "52")
        storage.get_team_events.return_value = [game]
        # Both teams' players are in the same event's box score -- only
        # team 61's own row should ever be considered for team 61.
        storage.get_player_game_stats_for_event.return_value = [
            _player_game("101", "61", game["event_key"], "2025-10-04", {"passing_attempts": 20}),
            _player_game("999", "52", game["event_key"], "2025-10-04", {"passing_attempts": 40}),
        ]
        storage.get_entity.return_value = _entity("61")
        storage.get_player_game_stats.return_value = [{"entity_id": "101"}]

        result = live_features._presumptive_leader(storage, "ncaafb", "61", "2025-10-11", 2025, "passing", 5)

        assert result[0] == "101"


class TestBuildLiveEventFeatures:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_event_features(storage, "ncaafb", "SPORT#NCAAFB#EVENT#1")

    def test_raises_when_missing_home_or_away(self):
        storage = MagicMock()
        storage.get_event.return_value = {
            "event_key": "SPORT#NCAAFB#EVENT#1", "event_date": "2025-10-11",
            "participants": [{"entity_id": "61", "role": "home"}],
        }
        with pytest.raises(live_features.MalformedEventError):
            live_features.build_live_event_features(storage, "ncaafb", "SPORT#NCAAFB#EVENT#1")

    def test_builds_a_feature_row_with_no_leaders_found(self):
        storage = MagicMock()
        event = _event("SPORT#NCAAFB#EVENT#1", "2025-10-11", "61", "52")
        storage.get_event.return_value = event
        storage.get_team_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_all_events.return_value = []
        storage.get_entity.return_value = None
        storage.get_player_game_stats_for_event.return_value = []

        row = live_features.build_live_event_features(storage, "ncaafb", event["event_key"])

        assert row["event_key"] == event["event_key"]
        assert row["home_qb_games_played"] == 0


class TestBuildLivePlayerFeatures:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_player_features(storage, "ncaafb", "SPORT#NCAAFB#EVENT#1", "101")

    def test_raises_when_entity_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("SPORT#NCAAFB#EVENT#1", "2025-10-11", "61", "52")
        storage.get_entity.return_value = None
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_player_features(storage, "ncaafb", "SPORT#NCAAFB#EVENT#1", "101")

    def test_builds_a_feature_row(self):
        storage = MagicMock()
        event = _event("SPORT#NCAAFB#EVENT#1", "2025-10-11", "61", "52")
        storage.get_event.return_value = event
        storage.get_entity.return_value = _entity("61")
        storage.get_player_game_stats.return_value = []
        storage.get_team_events.return_value = []
        storage.get_all_events.return_value = []

        row = live_features.build_live_player_features(storage, "ncaafb", event["event_key"], "101")

        assert row["entity_id"] == "101"
        assert row["team_id"] == "61"
        assert row["is_home"] is True


class TestBuildLiveEventLeaders:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_event_leaders(storage, "ncaafb", "SPORT#NCAAFB#EVENT#1")

    def test_shape_has_no_sacks_key(self):
        storage = MagicMock()
        event = _event("SPORT#NCAAFB#EVENT#1", "2025-10-11", "61", "52")
        storage.get_event.return_value = event
        storage.get_team_events.return_value = []
        storage.get_all_events.return_value = []
        storage.get_entity.return_value = None

        result = live_features.build_live_event_leaders(storage, "ncaafb", event["event_key"])

        assert set(result.keys()) == {"home", "away"}
        assert set(result["home"].keys()) == {"passing", "receiving", "rushing"}
        assert result["home"] == {"passing": None, "receiving": None, "rushing": None}

    def test_populates_a_found_leader(self):
        storage = MagicMock()
        event = _event("SPORT#NCAAFB#EVENT#1", "2025-10-11", "61", "52")
        past_game = _event("SPORT#NCAAFB#EVENT#900", "2025-10-04", "61", "70")
        storage.get_event.return_value = event
        storage.get_all_events.return_value = []

        def _team_events(sport, team_id, before_date=None, limit=None):
            return [past_game] if team_id == "61" else []

        storage.get_team_events.side_effect = _team_events
        storage.get_player_game_stats_for_event.return_value = [
            _player_game("101", "61", past_game["event_key"], "2025-10-04", {"passing_attempts": 30}),
        ]
        storage.get_entity.return_value = _entity("61")
        storage.get_player_game_stats.return_value = [{"entity_id": "101"}]

        result = live_features.build_live_event_leaders(storage, "ncaafb", event["event_key"])

        assert result["home"]["passing"]["entity_id"] == "101"
        assert result["away"]["passing"] is None
