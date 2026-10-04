"""
Unit tests for library.features.common's rolling-history stat primitives:
rest_days, rolling_team_scoring_averages, current_streak, and
rolling_player_stat_averages. No AWS involved. Split out of what used to
be one large test_common.py -- see test_common_elo.py's own history note.
"""
import pytest

from library.features.common import (
    current_streak,
    games_this_season,
    rest_days,
    rolling_player_stat_averages,
    rolling_team_scoring_averages,
    team_scoring_columns,
)


class TestRestDays:
    def test_none_when_no_previous_event(self):
        assert rest_days("2025-09-15", None) is None

    def test_computes_day_delta(self):
        assert rest_days("2025-09-15", "2025-09-08") == 7


class TestRollingTeamScoringAverages:
    def _game(self, event_date, own_score, opp_score, own_id="KC", opp_id="LAC"):
        return {
            "event_date": event_date,
            "participants": [
                {"entity_id": own_id, "result": {"score": own_score}},
                {"entity_id": opp_id, "result": {"score": opp_score}},
            ],
        }

    def test_averages_scored_and_allowed(self):
        team_events = [self._game("2025-09-15", 27, 20), self._game("2025-09-08", 24, 21)]

        result = rolling_team_scoring_averages(team_events, "KC")

        assert result["avg_points_scored"] == 25.5
        assert result["avg_points_allowed"] == 20.5
        assert result["games_played"] == 2

    def test_respects_window_using_most_recent_first_order(self):
        team_events = [self._game("2025-09-15", 27, 20), self._game("2025-09-08", 24, 21)]

        result = rolling_team_scoring_averages(team_events, "KC", window=1)

        assert result["avg_points_scored"] == 27
        assert result["avg_points_allowed"] == 20
        assert result["games_played"] == 1

    def test_empty_history_returns_none_averages(self):
        result = rolling_team_scoring_averages([], "KC")

        assert result["avg_points_scored"] is None
        assert result["avg_points_allowed"] is None
        assert result["games_played"] == 0

    def test_this_season_averages_leave_out_last_seasons_games(self):
        team_events = [
            self._game("2025-09-15", 30, 10), self._game("2025-09-08", 20, 20),
            self._game("2025-01-12", 0, 40), self._game("2024-12-29", 10, 30),
        ]

        result = rolling_team_scoring_averages(team_events, "KC", as_of="2025-09-22")

        assert result["avg_points_scored"] == 15
        assert result["games_this_season"] == 2
        assert result["avg_points_scored_this_season"] == 25
        assert result["avg_points_allowed_this_season"] == 15

    def test_week_one_has_no_games_this_season(self):
        result = rolling_team_scoring_averages([self._game("2025-01-12", 21, 14)], "KC", as_of="2025-09-07")

        assert result["games_this_season"] == 0
        assert result["avg_points_scored_this_season"] is None

    def test_without_a_date_there_is_no_season_split(self):
        result = rolling_team_scoring_averages([self._game("2025-09-15", 27, 20)], "KC")

        assert result["games_this_season"] is None
        assert result["avg_points_scored_this_season"] is None

    def test_vs_opponent_compares_each_game_with_that_opponents_usual(self):
        game = self._game("2025-09-15", 27, 20)
        game["event_key"] = "E9"
        game["participants"][1]["role"] = "away"
        pre_game = {"E9": {"away_pre_avg_points_allowed": 20.0, "away_pre_avg_points_scored": 25.0}}

        result = rolling_team_scoring_averages([game], "KC", pre_game=pre_game)

        assert result["avg_points_scored_vs_opponent"] == 7
        assert result["avg_points_allowed_vs_opponent"] == -5

    def test_vs_opponent_skips_games_without_opponent_history(self):
        game = self._game("2025-09-15", 27, 20)
        game["event_key"] = "E9"
        game["participants"][1]["role"] = "away"

        result = rolling_team_scoring_averages([game], "KC", pre_game={"E9": {"away_pre_avg_points_allowed": None}})

        assert result["avg_points_scored_vs_opponent"] is None

    def test_window_features_ignore_history_beyond_the_window(self):
        recent = [self._game(f"2025-10-{d:02d}", 30, 10) for d in (26, 19, 12, 5)]
        older = [self._game(f"2025-09-{d:02d}", 0, 50) for d in (28, 21, 14, 7)]

        result = rolling_team_scoring_averages(recent + older, "KC", window=4, as_of="2025-11-02")

        assert result["avg_points_scored"] == 30
        assert result["games_played"] == 4
        assert result["games_this_season"] == 4

    def test_last3_recency_weighted_and_season_to_date_use_the_whole_history(self):
        games = [
            self._game("2025-11-02", 30, 10), self._game("2025-10-26", 20, 20), self._game("2025-10-19", 10, 30),
            self._game("2025-10-12", 0, 40), self._game("2025-01-05", 99, 0),
        ]

        result = rolling_team_scoring_averages(games, "KC", window=1, as_of="2025-11-09")

        assert result["avg_points_scored_last3"] == 20
        assert result["avg_points_scored_season_to_date"] == 15
        assert result["avg_points_allowed_season_to_date"] == 25
        weights = [0.5 ** (age / 3) for age in range(5)]
        assert result["avg_points_scored_ewm"] == pytest.approx(sum(w * s for w, s in zip(weights, (30, 20, 10, 0, 99))) / sum(weights))

    def test_no_history_has_no_recency_averages(self):
        result = rolling_team_scoring_averages([], "KC", as_of="2025-11-09")

        assert result["avg_points_scored_ewm"] is None
        assert result["avg_points_scored_season_to_date"] is None

    def test_columns_are_prefixed_per_side(self):
        columns = team_scoring_columns({"games_played": 3}, {"games_played": 4})

        assert columns == {"home_games_played": 3, "away_games_played": 4}


class TestGamesThisSeason:
    @pytest.mark.parametrize("dates, expected", [
        (["2025-11-02", "2025-10-26"], 2),
        (["2025-11-02", "2025-06-01", "2025-05-25"], 1),
        (["2025-08-01"], 0),
        ([], 0),
    ])
    def test_counts_games_since_the_last_off_season_gap(self, dates, expected):
        assert games_this_season([{"event_date": d} for d in dates], "2025-11-09") == expected

    def test_counting_stops_at_a_game_with_no_date(self):
        assert games_this_season([{"event_date": "2025-11-02"}, {}, {"event_date": "2025-10-19"}], "2025-11-09") == 1

    def test_a_two_month_gap_is_still_the_same_season(self):
        assert games_this_season([{"event_date": "2025-11-01"}], "2025-12-31") == 1


class TestCurrentStreak:
    def _game(self, event_date, own_score, opp_score, own_id="KC", opp_id="LAC"):
        return {
            "event_date": event_date,
            "participants": [
                {"entity_id": own_id, "result": {"score": own_score}},
                {"entity_id": opp_id, "result": {"score": opp_score}},
            ],
        }

    def test_win_streak_is_positive(self):
        team_events = [  # most recent first
            self._game("2025-09-15", 27, 20),
            self._game("2025-09-08", 24, 21),
            self._game("2025-09-01", 17, 10),
        ]

        assert current_streak(team_events, "KC") == 3

    def test_loss_streak_is_negative(self):
        team_events = [
            self._game("2025-09-15", 10, 27),
            self._game("2025-09-08", 14, 21),
        ]

        assert current_streak(team_events, "KC") == -2

    def test_streak_stops_at_direction_change(self):
        team_events = [
            self._game("2025-09-15", 27, 20),  # win
            self._game("2025-09-08", 27, 20),  # win
            self._game("2025-09-01", 10, 27),  # loss -- streak ends here
        ]

        assert current_streak(team_events, "KC") == 2

    def test_tie_breaks_the_streak_rather_than_counting_as_a_loss(self):
        team_events = [
            self._game("2025-09-15", 20, 20),  # tie
            self._game("2025-09-08", 27, 20),  # win -- shouldn't be reached
        ]

        assert current_streak(team_events, "KC") == 0

    def test_empty_history_is_zero(self):
        assert current_streak([], "KC") == 0


class TestRollingPlayerStatAverages:
    def test_averages_each_key_only_over_games_that_have_it(self):
        games = [
            {"event_date": "2025-09-15", "stat_line": {"passing_yards": 300, "passing_tds": 2}, "started": True},
            {"event_date": "2025-09-08", "stat_line": {"passing_yards": 250}, "started": True},
        ]

        result = rolling_player_stat_averages(games)

        assert result["avg_passing_yards"] == 275
        assert result["avg_passing_tds"] == 2  # only one game had this key
        assert result["games_played"] == 2
        assert result["starts"] == 2
        assert result["games_with_passing_yards"] == 2
        assert result["games_with_passing_tds"] == 1

    def test_respects_window(self):
        games = [
            {"event_date": "2025-09-15", "stat_line": {"passing_yards": 300}, "started": True},
            {"event_date": "2025-09-08", "stat_line": {"passing_yards": 100}, "started": False},
        ]

        result = rolling_player_stat_averages(games, window=1)

        assert result["avg_passing_yards"] == 300
        assert result["games_played"] == 1
        assert result["starts"] == 1

    def test_ignores_non_numeric_stat_values(self):
        games = [{"event_date": "2025-09-15", "stat_line": {"position": "QB", "passing_yards": 300}, "started": True}]

        result = rolling_player_stat_averages(games)

        assert "avg_position" not in result
        assert "games_with_position" not in result
        assert result["avg_passing_yards"] == 300

    def test_empty_history(self):
        result = rolling_player_stat_averages([])

        assert result == {"games_played": 0, "starts": 0, "games_this_season": None}
