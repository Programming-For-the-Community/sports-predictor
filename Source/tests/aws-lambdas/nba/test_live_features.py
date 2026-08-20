"""
Unit tests for nba/predict/live_features.py. NBA's build_event_features
carries no leader-tracking sub-feature (see library.features.nba's own
docstring for why), so _box_score_candidate_ids is only ever used by the
leaders panel (build_live_event_leader_candidates), not by
build_live_event_features itself. FeatureStorage is mocked throughout.

The nba_predict module (and this directory's own sys.path entry for
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
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }


def _player_game(entity_id, team_id, event_key, event_date, stat_line):
    return {
        "entity_id": entity_id, "team_id": team_id, "event_key": event_key,
        "event_date": event_date, "player_key": f"SPORT#NBA#PLAYER#{entity_id}", "stat_line": stat_line,
    }


def _entity(team_id):
    return {"metadata": {"team_id": team_id}}


class TestStillOnTeam:
    def test_true_when_entity_team_matches(self):
        storage = MagicMock()
        storage.get_entity.return_value = _entity("13")
        assert live_features._still_on_team(storage, "nba", "101", "13") is True

    def test_false_when_entity_team_differs(self):
        storage = MagicMock()
        storage.get_entity.return_value = _entity("2")
        assert live_features._still_on_team(storage, "nba", "101", "13") is False

    def test_false_when_entity_missing(self):
        storage = MagicMock()
        storage.get_entity.return_value = None
        assert live_features._still_on_team(storage, "nba", "101", "13") is False


class TestBoxScoreCandidateIds:
    """Roster-membership checks (_still_on_team) now run concurrently --
    see the function's own docstring for why. These tests only lock in
    output correctness (right candidates, still-rostered filter, season-
    lookback bound), not the concurrency itself, which a synchronous unit
    test can't meaningfully observe."""

    def test_returns_still_rostered_candidates_credited_with_the_stat(self):
        storage = MagicMock()
        storage.get_team_events.return_value = [_event("e1", "2025-11-11", "13", "2")]
        storage.get_player_game_stats_for_event.return_value = [
            _player_game("101", "13", "e1", "2025-11-11", {"points": 28}),
            _player_game("102", "13", "e1", "2025-11-11", {"points": 12}),
        ]
        storage.get_entity.side_effect = lambda sport, entity_id, entity_type: {
            "101": _entity("13"), "102": _entity("99"),  # 102 has since been traded
        }[entity_id]

        ids = live_features._box_score_candidate_ids(storage, "nba", "13", "2025-11-18", 2025, "points")

        assert ids == ["101"]

    def test_skips_a_row_missing_the_stat_key(self):
        storage = MagicMock()
        storage.get_team_events.return_value = [_event("e1", "2025-11-11", "13", "2")]
        storage.get_player_game_stats_for_event.return_value = [
            _player_game("101", "13", "e1", "2025-11-11", {"assists": 6}),
        ]
        storage.get_entity.return_value = _entity("13")

        ids = live_features._box_score_candidate_ids(storage, "nba", "13", "2025-11-18", 2025, "points")

        assert ids == []
        storage.get_entity.assert_not_called()

    def test_empty_team_events_short_circuits_without_any_roster_check(self):
        storage = MagicMock()
        storage.get_team_events.return_value = []

        ids = live_features._box_score_candidate_ids(storage, "nba", "13", "2025-11-18", 2025, "points")

        assert ids == []
        storage.get_entity.assert_not_called()

    def test_a_candidate_appearing_in_multiple_games_is_only_checked_once(self):
        storage = MagicMock()
        storage.get_team_events.return_value = [
            _event("e1", "2025-11-11", "13", "2"),
            _event("e2", "2025-11-09", "13", "17"),
        ]
        storage.get_player_game_stats_for_event.side_effect = lambda event_key: {
            "e1": [_player_game("101", "13", "e1", "2025-11-11", {"points": 28})],
            "e2": [_player_game("101", "13", "e2", "2025-11-09", {"points": 30})],
        }[event_key]
        storage.get_entity.return_value = _entity("13")

        ids = live_features._box_score_candidate_ids(storage, "nba", "13", "2025-11-18", 2025, "points")

        assert ids == ["101"]
        storage.get_entity.assert_called_once()

    def test_stops_at_the_season_lookback_bound(self):
        storage = MagicMock()
        too_old = _event("e0", "2023-11-01", "13", "2", season=2023)
        storage.get_team_events.return_value = [too_old]

        ids = live_features._box_score_candidate_ids(storage, "nba", "13", "2025-11-18", 2025, "points")

        assert ids == []
        storage.get_player_game_stats_for_event.assert_not_called()


class TestBuildLiveEventFeatures:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_event_features(storage, "nba", "SPORT#NBA#EVENT#1")

    def test_raises_when_missing_home_or_away(self):
        storage = MagicMock()
        storage.get_event.return_value = {
            "event_key": "SPORT#NBA#EVENT#1", "event_date": "2025-11-11",
            "participants": [{"entity_id": "13", "role": "home"}],
        }
        with pytest.raises(live_features.MalformedEventError):
            live_features.build_live_event_features(storage, "nba", "SPORT#NBA#EVENT#1")

    def test_builds_a_feature_row_with_no_history(self):
        storage = MagicMock()
        event = _event("SPORT#NBA#EVENT#1", "2025-11-11", "13", "2")
        storage.get_event.return_value = event
        storage.get_team_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_all_events.return_value = []
        storage.get_all_team_game_stats.return_value = []

        row = live_features.build_live_event_features(storage, "nba", event["event_key"])

        assert row["event_key"] == event["event_key"]
        assert row["home_games_played"] == 0
        assert row["home_avg_rebounds"] is None


class TestBuildLivePlayerFeatures:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_player_features(storage, "nba", "SPORT#NBA#EVENT#1", "101")

    def test_raises_when_entity_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("SPORT#NBA#EVENT#1", "2025-11-11", "13", "2")
        storage.get_entity.return_value = None
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_player_features(storage, "nba", "SPORT#NBA#EVENT#1", "101")

    def test_builds_a_feature_row(self):
        storage = MagicMock()
        event = _event("SPORT#NBA#EVENT#1", "2025-11-11", "13", "2")
        storage.get_event.return_value = event
        storage.get_entity.return_value = _entity("13")
        storage.get_player_game_stats.return_value = []
        storage.get_team_events.return_value = []
        storage.get_all_events.return_value = []

        row = live_features.build_live_player_features(storage, "nba", event["event_key"], "101")

        assert row["entity_id"] == "101"
        assert row["team_id"] == "13"
        assert row["is_home"] is True


class TestBuildLiveEventLeaderCandidates:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_event_leader_candidates(storage, "nba", "SPORT#NBA#EVENT#1")

    def test_shape_has_three_categories_and_empty_lists_when_no_candidates(self):
        storage = MagicMock()
        event = _event("SPORT#NBA#EVENT#1", "2025-11-11", "13", "2")
        storage.get_event.return_value = event
        storage.get_team_events.return_value = []
        storage.get_all_events.return_value = []
        storage.get_entity.return_value = None

        result = live_features.build_live_event_leader_candidates(storage, "nba", event["event_key"])

        assert set(result.keys()) == {"home", "away"}
        assert set(result["home"].keys()) == {"scoring", "rebounding", "assists"}
        assert result["home"] == {"scoring": [], "rebounding": [], "assists": []}

    def test_ranks_multiple_scoring_candidates_by_recent_volume(self):
        storage = MagicMock()
        event = _event("SPORT#NBA#EVENT#1", "2025-11-11", "13", "2")
        past_game = _event("SPORT#NBA#EVENT#900", "2025-11-09", "13", "17")
        storage.get_event.return_value = event
        storage.get_all_events.return_value = []

        def _team_events(sport, team_id, before_date=None, limit=None, events=None):
            return [past_game] if team_id == "13" else []

        storage.get_team_events.side_effect = _team_events
        storage.get_player_game_stats_for_event.return_value = [
            _player_game("101", "13", past_game["event_key"], "2025-11-09", {"points": 8}),
            _player_game("102", "13", past_game["event_key"], "2025-11-09", {"points": 30}),
            _player_game("103", "13", past_game["event_key"], "2025-11-09", {"points": 18}),
            _player_game("104", "13", past_game["event_key"], "2025-11-09", {"points": 22}),
            _player_game("105", "13", past_game["event_key"], "2025-11-09", {"points": 15}),
            _player_game("106", "13", past_game["event_key"], "2025-11-09", {"points": 4}),
        ]
        storage.get_entity.return_value = _entity("13")

        def _player_history(entity_id, before_date=None, limit=None):
            volumes = {"101": 8, "102": 30, "103": 18, "104": 22, "105": 15, "106": 4}
            return [{"entity_id": entity_id, "stat_line": {"points": volumes[entity_id]}}]

        storage.get_player_game_stats.side_effect = _player_history

        result = live_features.build_live_event_leader_candidates(storage, "nba", event["event_key"])

        # LEADER_CANDIDATE_LIMITS caps scoring at 5 -- the top 5 by volume, not 106 (lowest).
        assert [row["entity_id"] for row in result["home"]["scoring"]] == ["102", "104", "103", "105", "101"]
        assert result["away"]["scoring"] == []
