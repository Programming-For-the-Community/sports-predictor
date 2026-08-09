"""
Unit tests for library.features.nfl.build_event_features' rolling-history
derived fields: QB/RB/WR per-position averages (from their own presumptive-
leader game history), team box-score averages (turnovers/yards/possession/
third-down/red-zone/penalties), and win streak. No AWS involved. Split out
of what used to be one large test_nfl.py -- see
test_nfl_event_features_core.py's own history note.
"""
import pytest

from library.features.nfl import build_event_features

from _nfl_test_helpers import event as _event


class TestBuildEventFeaturesRollingStats:
    def test_qb_rolling_stats_are_computed_from_qb_games(self):
        # stat_line keys here match what normalize.py actually produces for
        # the passing category -- "passing_yards"/"passing_touchdowns"
        # (not double-prefixed) but "passing_interceptions" (prefixed, to
        # stay distinct from the "interceptions" category's own bare
        # "interceptions" key) -- see the comment in build_event_features.
        # Using the real key names is the point of this test: getting them
        # wrong is exactly what silently zeroed out these columns in
        # production.
        event = _event("E2", "2025-09-14", "KC", "LAC", 20, 17)
        home_qb_games = [
            {
                "event_date": "2025-09-07",
                "stat_line": {"passing_yards": 300, "passing_touchdowns": 2, "passing_interceptions": 1},
                "started": True,
            },
        ]

        row = build_event_features(event, {}, [], [], home_qb_games=home_qb_games)

        assert row["home_qb_avg_passing_yards"] == 300
        assert row["home_qb_avg_passing_tds"] == 2
        assert row["home_qb_avg_interceptions"] == 1
        assert row["home_qb_games_played"] == 1
        assert row["away_qb_games_played"] == 0
        assert row["away_qb_avg_passing_yards"] is None

    def test_qb_rolling_stats_default_to_none_when_no_qb_games_given(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["home_qb_avg_passing_yards"] is None
        assert row["home_qb_games_played"] == 0
        assert row["away_qb_avg_passing_yards"] is None
        assert row["away_qb_games_played"] == 0

    def test_rb_and_wr_rolling_stats_are_computed_from_their_own_games(self):
        event = _event("E2", "2025-09-14", "KC", "LAC", 20, 17)
        home_rb_games = [
            {"event_date": "2025-09-07", "stat_line": {"rushing_yards": 95, "rushing_touchdowns": 1}, "started": True},
        ]
        home_wr_games = [
            {
                "event_date": "2025-09-07",
                "stat_line": {"receiving_yards": 110, "receiving_touchdowns": 1, "receiving_receptions": 6},
                "started": True,
            },
        ]

        row = build_event_features(
            event, {}, [], [], home_rb_games=home_rb_games, home_wr_games=home_wr_games,
        )

        assert row["home_rb_avg_rushing_yards"] == 95
        assert row["home_rb_avg_rushing_tds"] == 1
        assert row["home_rb_games_played"] == 1
        assert row["home_wr_avg_receiving_yards"] == 110
        assert row["home_wr_avg_receiving_tds"] == 1
        assert row["home_wr_avg_receptions"] == 6
        assert row["home_wr_games_played"] == 1
        assert row["away_rb_games_played"] == 0
        assert row["away_wr_games_played"] == 0

    def test_rb_and_wr_rolling_stats_default_to_none_when_no_games_given(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["home_rb_avg_rushing_yards"] is None
        assert row["home_rb_games_played"] == 0
        assert row["home_wr_avg_receiving_yards"] is None
        assert row["home_wr_games_played"] == 0
        assert row["away_rb_avg_rushing_yards"] is None
        assert row["away_wr_avg_receiving_yards"] is None

    def test_team_box_stats_are_computed_from_team_game_stats_history(self):
        event = _event("E2", "2025-09-14", "KC", "LAC", 20, 17)
        home_box_history = [
            {
                "event_date": "2025-09-07",
                "stat_line": {
                    "turnovers": 1, "total_yards": 350, "possession_time_seconds": 1800,
                    "third_down_conversions": 6, "third_down_attempts": 12,
                    "red_zone_conversions": 2, "red_zone_attempts": 4,
                    "penalties": 5, "penalty_yards": 45,
                },
            },
        ]

        row = build_event_features(event, {}, [], [], home_team_box_stats=home_box_history)

        assert row["home_avg_turnovers"] == 1
        assert row["home_avg_total_yards"] == 350
        assert row["home_avg_possession_time_seconds"] == 1800
        assert row["home_avg_penalties"] == 5
        assert row["home_avg_penalty_yards"] == 45
        assert row["home_third_down_pct"] == 0.5
        assert row["home_red_zone_pct"] == 0.5
        assert row["home_box_games_played"] == 1
        assert row["away_box_games_played"] == 0

    def test_team_box_stats_default_to_none_when_no_history_given(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["home_avg_turnovers"] is None
        assert row["home_avg_penalties"] is None
        assert row["home_avg_penalty_yards"] is None
        assert row["home_third_down_pct"] is None
        assert row["home_red_zone_pct"] is None
        assert row["home_box_games_played"] == 0
        assert row["away_avg_turnovers"] is None

    def test_third_down_pct_is_conversions_over_attempts_across_the_window_not_averaged_per_game(self):
        # 1/1 in one game and 1/9 in another should read as 2/10 = 0.2,
        # not the unweighted average of the two per-game rates (0.5 and
        # 0.111 -> ~0.31), which would let a tiny-sample game dominate.
        home_box_history = [
            {"event_date": "2025-09-14", "stat_line": {"third_down_conversions": 1, "third_down_attempts": 1}},
            {"event_date": "2025-09-07", "stat_line": {"third_down_conversions": 1, "third_down_attempts": 9}},
        ]
        event = _event("E3", "2025-09-21", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [], home_team_box_stats=home_box_history)

        assert row["home_third_down_pct"] == pytest.approx(0.2)

    def test_win_streak_fields_reflect_team_history(self):
        event = _event("E3", "2025-09-21", "KC", "LAC", 27, 20)
        home_history = [
            {"event_date": "2025-09-14", "participants": [
                {"entity_id": "KC", "result": {"score": 27}}, {"entity_id": "DET", "result": {"score": 20}},
            ]},
            {"event_date": "2025-09-07", "participants": [
                {"entity_id": "KC", "result": {"score": 24}}, {"entity_id": "NYJ", "result": {"score": 17}},
            ]},
        ]

        row = build_event_features(event, {}, home_history, [])

        assert row["home_win_streak"] == 2
        assert row["away_win_streak"] == 0
