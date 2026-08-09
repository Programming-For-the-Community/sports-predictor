"""
Unit tests for library.features.common's rolling-history stat primitives:
rest_days, rolling_team_scoring_averages, current_streak, and
rolling_player_stat_averages. No AWS involved. Split out of what used to
be one large test_common.py -- see test_common_elo.py's own history note.
"""
from library.features.common import current_streak, rest_days, rolling_player_stat_averages, rolling_team_scoring_averages


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

        assert result == {"games_played": 0, "starts": 0}
