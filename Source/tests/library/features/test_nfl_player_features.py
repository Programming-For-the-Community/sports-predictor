"""
Unit tests for library.features.nfl.build_player_features -- the
single-player feature row (own/opponent Elo, home/away context,
kickoff/venue/weather, divisional/travel) both the training pipeline and
live_features.build_live_player_features assemble around. No AWS
involved. Split out of what used to be one large test_nfl.py -- see
test_nfl_event_features_core.py's own history note.
"""
from library.features.nfl import build_player_features

from _nfl_test_helpers import event as _event


class TestBuildPlayerFeatures:
    def _player_game(self, **overrides):
        base = {
            "event_key": "E2",
            "player_key": "PLAYER#mahomes-patrick",
            "entity_id": "mahomes-patrick",
            "team_id": "KC",
            "event_date": "2025-09-14",
            "stat_line": {"passing_yards": 310, "passing_tds": 3},
            "started": True,
        }
        return {**base, **overrides}

    def test_assembles_expected_fields(self):
        player_game = self._player_game()
        prior_games = [
            {"event_date": "2025-09-07", "stat_line": {"passing_yards": 280}, "started": True},
        ]
        event = _event("E2", "2025-09-14", "KC", "LAC", 27, 20, week=2, season_type=2)
        elo_ratings = {"E2": {"home_pre_rating": 1550.0, "away_pre_rating": 1490.0}}

        row = build_player_features(player_game, prior_games, event, elo_ratings, "2025-09-07")

        assert row["entity_id"] == "mahomes-patrick"
        assert row["avg_passing_yards"] == 280
        assert row["games_played"] == 1
        assert row["label_stat_line"] == {"passing_yards": 310, "passing_tds": 3}
        assert row["label_started"] is True

    def test_home_side_gets_home_context(self):
        # KC is home in this event, and team_id matches KC.
        player_game = self._player_game(team_id="KC")
        event = _event("E2", "2025-09-14", "KC", "LAC", 27, 20, week=2, season_type=2)
        elo_ratings = {"E2": {"home_pre_rating": 1550.0, "away_pre_rating": 1490.0}}

        row = build_player_features(player_game, [], event, elo_ratings, "2025-09-07")

        assert row["is_home"] is True
        assert row["opponent_id"] == "LAC"
        assert row["own_elo"] == 1550.0
        assert row["opponent_elo"] == 1490.0
        assert row["elo_diff"] == 60.0
        assert row["week"] == 2
        assert row["season_type"] == 2
        assert row["rest_days"] == 7

    def test_surfaces_kickoff_hour_utc_from_the_event(self):
        player_game = self._player_game()
        event = _event("E2", "2025-09-14", "KC", "LAC", 27, 20, kickoff_time="2025-09-14T17:00Z")
        elo_ratings = {"E2": {"home_pre_rating": 1550.0, "away_pre_rating": 1490.0}}

        row = build_player_features(player_game, [], event, elo_ratings, "2025-09-07")

        assert row["kickoff_hour_utc"] == 17

    def test_away_side_gets_away_context_not_home(self):
        # Same event, but team_id now matches the away side -- own/opponent
        # must flip, not just default to the home perspective.
        player_game = self._player_game(team_id="LAC")
        event = _event("E2", "2025-09-14", "KC", "LAC", 27, 20)
        elo_ratings = {"E2": {"home_pre_rating": 1550.0, "away_pre_rating": 1490.0}}

        row = build_player_features(player_game, [], event, elo_ratings, None)

        assert row["is_home"] is False
        assert row["opponent_id"] == "KC"
        assert row["own_elo"] == 1490.0
        assert row["opponent_elo"] == 1550.0
        assert row["elo_diff"] == -60.0
        assert row["rest_days"] is None

    def test_venue_and_weather_are_surfaced(self):
        player_game = self._player_game()
        event = _event("E2", "2025-09-14", "KC", "LAC", 27, 20, venue_indoor=True, weather_temperature=None)
        elo_ratings = {}

        row = build_player_features(player_game, [], event, elo_ratings, None)

        assert row["venue_indoor"] is True
        assert row["weather_temperature"] is None

    def test_divisional_and_travel_use_real_team_ids(self):
        # "12"/"13" are KC/LV's real ESPN team ids (both AFC West) -- same
        # ids the event-features travel tests use.
        player_game = self._player_game(team_id="13")
        event = _event("E1", "2025-09-07", "12", "13", 27, 20)
        elo_ratings = {}

        row = build_player_features(player_game, [], event, elo_ratings, None)

        assert row["is_divisional_game"] is True
        assert row["travel_km"] > 0  # "13" (LV) is the away side here
