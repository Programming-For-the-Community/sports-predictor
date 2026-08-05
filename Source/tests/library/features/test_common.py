"""
Unit tests for library.features.common's sport-agnostic feature-computation
functions. No AWS involved -- every function here takes already-fetched
rows and returns numbers, so these tests just hand-build the generic
event/participant shape library.normalize.espn produces.
"""
import pytest

from library.features.common import (
    _injury_status_ordinal,
    _mov_multiplier,
    _team_injury_count,
    compute_elo_ratings,
    current_streak,
    expected_score,
    rank_by_average_stat,
    rest_days,
    rolling_player_stat_averages,
    rolling_team_scoring_averages,
)


def _event(event_key, event_date, home_id, away_id, home_score=None, away_score=None):
    home_result = {"score": home_score, "won": home_score is not None and home_score > away_score}
    away_result = {"score": away_score, "won": away_score is not None and away_score > home_score}
    return {
        "event_key": event_key,
        "event_date": event_date,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }


class TestComputeEloRatings:
    def test_first_meeting_starts_both_teams_at_starting_rating(self):
        events = [_event("E1", "2025-09-07", "KC", "LAC", 27, 20)]

        ratings, _ = compute_elo_ratings(events, starting_rating=1500)

        assert ratings["E1"]["home_pre_rating"] == 1500
        assert ratings["E1"]["away_pre_rating"] == 1500

    def test_home_win_updates_ratings_per_elo_formula(self):
        # expected_home = 1 / (1 + 10^((1500 - (1500+55)) / 400)) ~= 0.5785
        # mov_multiplier for a 7-point win with a 55-point winner_elo_diff
        # (the home team's own pre-game edge, home advantage included)
        # ~= 2.0287 -- see _mov_multiplier. E2's pre-game rating reflects
        # E1's MOV-scaled update.
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 27, 20),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        ratings, _ = compute_elo_ratings(events, k_factor=20, home_advantage=55, starting_rating=1500)

        assert ratings["E2"]["home_pre_rating"] == pytest.approx(1517.10, abs=0.05)
        assert ratings["E2"]["away_pre_rating"] == pytest.approx(1482.90, abs=0.05)

    def test_bigger_margin_moves_ratings_more(self):
        # compute_elo_ratings only exposes PRE-game ratings, so the
        # movement from E1 is read via a follow-up event's pre-game rating.
        followup = _event("E2", "2025-09-14", "KC", "LAC", 20, 17)
        close_win = [_event("E1", "2025-09-07", "KC", "LAC", 24, 20), followup]
        blowout = [_event("E1", "2025-09-07", "KC", "LAC", 45, 3), followup]

        close_ratings, _ = compute_elo_ratings(close_win, starting_rating=1500)
        blowout_ratings, _ = compute_elo_ratings(blowout, starting_rating=1500)

        close_gain = close_ratings["E2"]["home_pre_rating"] - 1500
        blowout_gain = blowout_ratings["E2"]["home_pre_rating"] - 1500
        assert blowout_gain > close_gain > 0

    def test_underdog_blowout_moves_ratings_more_than_favorite_blowout(self):
        # Tested directly against the multiplier -- compute_elo_ratings
        # has no way to seed teams at a non-starting rating.
        favorite_mult = _mov_multiplier(28, winner_elo_diff=600, base=2.2, divisor=0.001)
        underdog_mult = _mov_multiplier(28, winner_elo_diff=-600, base=2.2, divisor=0.001)

        assert underdog_mult > favorite_mult

    def test_tie_applies_no_mov_scaling(self):
        # A tie has no winner to measure a margin from -- the multiplier
        # must be a flat 1.0, not the ln(0+1)=0 the formula would
        # otherwise produce.
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 20, 20),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        ratings, _ = compute_elo_ratings(events, starting_rating=1500, home_advantage=10)

        # With home_advantage=10 (unequal expected outcome) and a tied
        # result, ratings should still move by exactly k_factor * (0.5 -
        # expected) -- i.e. multiplier == 1.0, not 0.
        expected_home = 1 / (1 + 10 ** ((1500 - (1500 + 10)) / 400))
        expected_move = 20.0 * (0.5 - expected_home)
        assert ratings["E2"]["home_pre_rating"] == pytest.approx(1500 + expected_move, abs=0.01)

    def test_away_win_decreases_home_rating(self):
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 17, 24),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        ratings, _ = compute_elo_ratings(events, starting_rating=1500)

        assert ratings["E2"]["home_pre_rating"] < 1500

    def test_processes_chronologically_regardless_of_input_order(self):
        earlier = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)
        later = _event("E2", "2025-09-14", "KC", "LAC", 20, 17)

        forward, _ = compute_elo_ratings([earlier, later], starting_rating=1500)
        reversed_input, _ = compute_elo_ratings([later, earlier], starting_rating=1500)

        assert forward["E2"]["home_pre_rating"] == reversed_input["E2"]["home_pre_rating"]

    def test_tie_moves_both_ratings_toward_each_other_evenly_when_equal(self):
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 20, 20),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        ratings, _ = compute_elo_ratings(events, starting_rating=1500, home_advantage=0)

        # Equal pre-game ratings, no home advantage, tied result -> no change.
        assert ratings["E2"]["home_pre_rating"] == pytest.approx(1500, abs=0.01)
        assert ratings["E2"]["away_pre_rating"] == pytest.approx(1500, abs=0.01)

    def test_event_missing_home_or_away_role_is_skipped_without_error(self):
        malformed = {
            "event_key": "BAD",
            "event_date": "2025-09-07",
            "participants": [{"entity_id": "KC", "role": "home", "result": {"score": 10}}],
        }
        events = [malformed, _event("E1", "2025-09-14", "KC", "LAC", 27, 20)]

        ratings, _ = compute_elo_ratings(events, starting_rating=1500)

        assert "BAD" not in ratings
        assert ratings["E1"]["home_pre_rating"] == 1500

    def test_event_missing_score_records_pre_rating_but_skips_update(self):
        scheduled = _event("E1", "2025-09-07", "KC", "LAC")  # no scores
        played = _event("E2", "2025-09-14", "KC", "LAC", 27, 20)

        ratings, _ = compute_elo_ratings([scheduled, played], starting_rating=1500)

        assert ratings["E1"]["home_pre_rating"] == 1500
        # Unaffected by the scoreless event -- still starting_rating.
        assert ratings["E2"]["home_pre_rating"] == 1500

    def test_current_ratings_reflect_every_processed_event_not_just_pre_game(self):
        # current_ratings (the second return value) is each team's rating
        # AFTER all events passed in -- e.g. for a live inference request
        # about a not-yet-played game, which has no pre_game_ratings entry
        # of its own to look up.
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 27, 20),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        _, current_ratings = compute_elo_ratings(events, starting_rating=1500)

        assert current_ratings["KC"] > 1500  # won both games
        assert current_ratings["LAC"] < 1500  # lost both games

    def test_current_ratings_for_a_team_with_no_events_is_absent(self):
        events = [_event("E1", "2025-09-07", "KC", "LAC", 27, 20)]

        _, current_ratings = compute_elo_ratings(events, starting_rating=1500)

        assert "DEN" not in current_ratings


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


class TestExpectedScore:
    def test_equal_ratings_no_advantage_is_a_coin_flip(self):
        assert expected_score(1500, 1500) == pytest.approx(0.5)

    def test_higher_rating_favored(self):
        assert expected_score(1600, 1500) > 0.5

    def test_home_advantage_shifts_probability_toward_the_home_side(self):
        even = expected_score(1500, 1500, rating_advantage=0)
        with_advantage = expected_score(1500, 1500, rating_advantage=55)

        assert with_advantage > even

    def test_symmetric_with_its_opponent(self):
        home = expected_score(1500, 1600, rating_advantage=55)
        away = expected_score(1600, 1500, rating_advantage=-55)

        assert home == pytest.approx(1 - away)


class TestRankByAverageStat:
    def test_ranks_by_average_not_a_single_huge_game(self):
        # "player-b" had one huge game but a lower average overall --
        # this is exactly the case single-game volume (_identify_leader's
        # approach) would get wrong for a bursty stat like sacks.
        histories = {
            "player-a": [
                {"stat_line": {"defensive_sacks": 2.0}},
                {"stat_line": {"defensive_sacks": 2.0}},
                {"stat_line": {"defensive_sacks": 2.0}},
            ],
            "player-b": [
                {"stat_line": {"defensive_sacks": 5.0}},
                {"stat_line": {"defensive_sacks": 0.0}},
                {"stat_line": {"defensive_sacks": 0.0}},
            ],
        }

        ranked = rank_by_average_stat(histories, "defensive_sacks", n=2)

        assert ranked == ["player-a", "player-b"]

    def test_candidates_with_no_recorded_value_are_excluded(self):
        histories = {"offense-player": [{"stat_line": {"passing_yards": 250}}]}

        assert rank_by_average_stat(histories, "defensive_sacks", n=3) == []

    def test_respects_n(self):
        histories = {
            "a": [{"stat_line": {"defensive_sacks": 3.0}}],
            "b": [{"stat_line": {"defensive_sacks": 2.0}}],
            "c": [{"stat_line": {"defensive_sacks": 1.0}}],
        }

        assert rank_by_average_stat(histories, "defensive_sacks", n=1) == ["a"]


class TestInjuryStatusOrdinal:
    def test_none_when_injuries_is_none(self):
        assert _injury_status_ordinal(None, "mahomes") is None

    def test_none_when_entity_id_is_none(self):
        assert _injury_status_ordinal([{"entity_id": "mahomes", "status": "Out"}], None) is None

    def test_zero_when_player_not_on_the_report(self):
        assert _injury_status_ordinal([{"entity_id": "someone-else", "status": "Out"}], "mahomes") == 0

    def test_zero_when_report_is_empty_list(self):
        assert _injury_status_ordinal([], "mahomes") == 0

    @pytest.mark.parametrize("status,expected", [("Questionable", 1), ("Doubtful", 2), ("Out", 3)])
    def test_maps_known_statuses_to_severity_order(self, status, expected):
        assert _injury_status_ordinal([{"entity_id": "mahomes", "status": status}], "mahomes") == expected

    def test_unrecognized_status_falls_back_to_1(self):
        assert _injury_status_ordinal([{"entity_id": "mahomes", "status": "Injured Reserve"}], "mahomes") == 1


class TestTeamInjuryCount:
    def test_none_when_injuries_is_none(self):
        assert _team_injury_count(None) is None

    def test_zero_for_empty_report(self):
        assert _team_injury_count([]) == 0

    def test_counts_only_doubtful_and_out(self):
        injuries = [
            {"entity_id": "1", "status": "Out"},
            {"entity_id": "2", "status": "Doubtful"},
            {"entity_id": "3", "status": "Questionable"},
            {"entity_id": "4", "status": "Out"},
        ]
        assert _team_injury_count(injuries) == 3
