"""
Unit tests for library.features.ncaafb.build_player_features -- same
calling convention as library.features.nfl.build_player_features, plus
the dynamic team_coordinates argument (see build_event_features' own
test file for why).
"""
from library.features.ncaafb import build_player_features

from _ncaafb_test_helpers import event as _event

_COORDS = {"333": (33.2083, -87.5504), "61": (33.9498, -83.3733)}


class TestBuildPlayerFeatures:
    def _player_game(self, **overrides):
        base = {
            "event_key": "E2",
            "player_key": "PLAYER#carson-beck",
            "entity_id": "carson-beck",
            "team_id": "333",
            "event_date": "2025-09-20",
            "stat_line": {"passing_yards": 310, "passing_touchdowns": 3},
            "started": True,
        }
        return {**base, **overrides}

    def test_assembles_expected_fields(self):
        player_game = self._player_game()
        prior_games = [{"event_date": "2025-09-13", "stat_line": {"passing_yards": 280}, "started": True}]
        event = _event("E2", "2025-09-20", "333", "61")
        elo_ratings = {"E2": {"home_pre_rating": 1600.0, "away_pre_rating": 1550.0}}

        row = build_player_features(player_game, prior_games, event, elo_ratings, "2025-09-13", _COORDS)

        assert row["entity_id"] == "carson-beck"
        assert row["avg_passing_yards"] == 280
        assert row["label_stat_line"] == {"passing_yards": 310, "passing_touchdowns": 3}

    def test_home_side_gets_home_context(self):
        player_game = self._player_game(team_id="333")
        event = _event("E2", "2025-09-20", "333", "61")
        elo_ratings = {"E2": {"home_pre_rating": 1600.0, "away_pre_rating": 1550.0}}

        row = build_player_features(player_game, [], event, elo_ratings, "2025-09-13", _COORDS)

        assert row["is_home"] is True
        assert row["opponent_id"] == "61"
        assert row["own_elo"] == 1600.0
        assert row["opponent_elo"] == 1550.0

    def test_away_side_gets_away_context_not_home(self):
        player_game = self._player_game(team_id="61")
        event = _event("E2", "2025-09-20", "333", "61")
        elo_ratings = {"E2": {"home_pre_rating": 1600.0, "away_pre_rating": 1550.0}}

        row = build_player_features(player_game, [], event, elo_ratings, None, _COORDS)

        assert row["is_home"] is False
        assert row["opponent_id"] == "333"
        assert row["own_elo"] == 1550.0
        assert row["rest_days"] is None

    def test_conference_bowl_playoff_fields_surfaced(self):
        player_game = self._player_game()
        event = _event("E2", "2025-09-20", "333", "61", conference_game=True, is_playoff_game=False, season_type="regular")

        row = build_player_features(player_game, [], event, {}, None, _COORDS)

        assert row["is_conference_game"] is True
        assert row["is_bowl_game"] is False
        assert row["is_playoff_game"] is False

    def test_travel_km_uses_dynamic_coordinates(self):
        player_game = self._player_game(team_id="61")
        event = _event("E1", "2025-09-13", "333", "61")

        row = build_player_features(player_game, [], event, {}, None, _COORDS)

        assert row["travel_km"] > 0  # "61" (Georgia) is the away side here
