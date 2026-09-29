"""
library.features.common's handling of partial/malformed events -- a
missing side, a missing score, or an unparseable timestamp is skipped (or
ends a streak) rather than raising or skewing an average.
"""
from library.features.common import (
    _season_record,
    average_opponent_elo,
    current_streak,
    kickoff_hour_utc,
    rolling_team_scoring_averages,
)


def _game(event_key, own_score, opp_score, own_role="home"):
    opp_role = "away" if own_role == "home" else "home"
    return {
        "event_key": event_key,
        "participants": [
            {"entity_id": "T", "role": own_role, "result": {"score": own_score}},
            {"entity_id": "O", "role": opp_role, "result": {"score": opp_score}},
        ],
    }


_SOLO = {"event_key": "solo", "participants": [{"entity_id": "T", "role": "home", "result": {"score": 10}}]}


class TestKickoffHourUtc:
    def test_parses_zulu_and_offset_timestamps(self):
        assert kickoff_hour_utc("2025-09-07T17:00Z") == 17
        assert kickoff_hour_utc("2025-09-07T20:30:00+00:00") == 20

    def test_missing_or_unparseable_is_none(self):
        assert kickoff_hour_utc(None) is None
        assert kickoff_hour_utc("") is None
        assert kickoff_hour_utc("TBD") is None


class TestRollingTeamScoringAverages:
    def test_skips_games_missing_a_side_or_a_score(self):
        averages = rolling_team_scoring_averages([_SOLO, _game("g1", 20, None), _game("g2", 30, 10)], "T")

        assert averages["avg_points_scored"] == 30
        assert averages["avg_points_allowed"] == 10


class TestCurrentStreak:
    def test_an_unscored_game_ends_the_streak(self):
        assert current_streak([_game("g1", 21, 14), _game("g2", None, 3), _game("g3", 30, 0)], "T") == 1


class TestSeasonRecord:
    def test_ignores_missing_sides_missing_scores_and_ties(self):
        events = [_SOLO, _game("g1", 7, None), _game("g2", 10, 10), _game("g3", 20, 3), _game("g4", 3, 20)]

        assert _season_record(events, "T") == (1, 1)


class TestAverageOpponentElo:
    def test_skips_games_missing_a_side_or_not_involving_the_team(self):
        other_teams = {"event_key": "x", "participants": [{"entity_id": "A", "role": "home"}, {"entity_id": "B", "role": "away"}]}
        elo = {
            "g1": {"home_pre_rating": 1500.0, "away_pre_rating": 1600.0},
            "g2": {"home_pre_rating": 1400.0, "away_pre_rating": 1550.0},
            "x": {"home_pre_rating": 9999.0, "away_pre_rating": 9999.0},
        }

        result = average_opponent_elo(
            [_SOLO, other_teams, _game("g1", 1, 0, own_role="home"), _game("g2", 1, 0, own_role="away")], "T", elo,
        )

        assert result == (1600.0 + 1400.0) / 2

    def test_none_when_no_opponent_rating_is_known(self):
        assert average_opponent_elo([_game("g1", 1, 0)], "T", {}) is None
