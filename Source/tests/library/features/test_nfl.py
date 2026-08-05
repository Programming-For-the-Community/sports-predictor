"""
Unit tests for library.features.nfl's pure feature-computation functions.
No AWS involved -- every function here takes already-fetched rows and
returns numbers, so these tests just hand-build the row shapes
FeatureStorage would return.
"""
import pytest

from library.features.nfl import (
    _injury_status_ordinal,
    _mov_multiplier,
    _team_injury_count,
    build_event_features,
    build_player_features,
    compute_elo_ratings,
    current_streak,
    expected_score,
    identify_lead_receiver,
    identify_lead_rusher,
    identify_starting_qb,
    identify_top_receivers,
    identify_top_rushers,
    rank_by_average_stat,
    rest_days,
    rolling_player_stat_averages,
    rolling_team_scoring_averages,
)


def _event(
    event_key, event_date, home_id, away_id, home_score=None, away_score=None, week=1, season_type=2,
    venue_indoor=None, venue_city=None, venue_state=None, weather_temperature=None,
    home_coach_experience=None, away_coach_experience=None,
    home_coach_season_win_pct=None, away_coach_season_win_pct=None,
    home_injuries=None, away_injuries=None,
):
    home_result = {"score": home_score, "won": home_score is not None and home_score > away_score}
    away_result = {"score": away_score, "won": away_score is not None and away_score > home_score}
    return {
        "event_key": event_key,
        "event_date": event_date,
        "week": week,
        "season_type": season_type,
        "venue_indoor": venue_indoor,
        "venue_city": venue_city,
        "venue_state": venue_state,
        "weather_temperature": weather_temperature,
        "home_coach_experience": home_coach_experience,
        "away_coach_experience": away_coach_experience,
        "home_coach_season_win_pct": home_coach_season_win_pct,
        "away_coach_season_win_pct": away_coach_season_win_pct,
        "home_injuries": home_injuries,
        "away_injuries": away_injuries,
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


class TestIdentifyStartingQb:
    def test_picks_most_passing_attempts(self):
        team_games = [
            {"entity_id": "backup-qb", "stat_line": {"passing_attempts": 5, "passing_yards": 40}},
            {"entity_id": "starter-qb", "stat_line": {"passing_attempts": 30, "passing_yards": 280}},
        ]

        starter = identify_starting_qb(team_games)

        assert starter["entity_id"] == "starter-qb"

    def test_returns_full_row_not_just_id(self):
        team_games = [{"entity_id": "starter-qb", "stat_line": {"passing_attempts": 30, "passing_yards": 280}}]

        starter = identify_starting_qb(team_games)

        assert starter == team_games[0]

    def test_ignores_non_passers(self):
        team_games = [
            {"entity_id": "kicker", "stat_line": {"field_goals_made": 2}},
            {"entity_id": "rb", "stat_line": {"rushing_yards": 80}},
        ]

        assert identify_starting_qb(team_games) is None

    def test_empty_team_games_returns_none(self):
        assert identify_starting_qb([]) is None


class TestIdentifyLeadRusher:
    def test_picks_most_rushing_attempts(self):
        team_games = [
            {"entity_id": "backup-rb", "stat_line": {"rushing_attempts": 3, "rushing_yards": 10}},
            {"entity_id": "lead-rb", "stat_line": {"rushing_attempts": 22, "rushing_yards": 95}},
        ]

        leader = identify_lead_rusher(team_games)

        assert leader["entity_id"] == "lead-rb"

    def test_ignores_players_without_rushing_attempts(self):
        team_games = [{"entity_id": "kicker", "stat_line": {"field_goals_made": 2}}]

        assert identify_lead_rusher(team_games) is None

    def test_empty_team_games_returns_none(self):
        assert identify_lead_rusher([]) is None


class TestIdentifyLeadReceiver:
    def test_picks_most_receiving_targets(self):
        team_games = [
            {"entity_id": "wr2", "stat_line": {"receiving_targets": 3, "receiving_yards": 20}},
            {"entity_id": "wr1", "stat_line": {"receiving_targets": 11, "receiving_yards": 130}},
        ]

        leader = identify_lead_receiver(team_games)

        assert leader["entity_id"] == "wr1"

    def test_picks_by_targets_not_receptions(self):
        # A player targeted often but with a low catch rate is still the
        # #1 read -- targets reflect who the offense looked for, not who
        # caught the most.
        team_games = [
            {"entity_id": "sure-hands", "stat_line": {"receiving_targets": 4, "receiving_receptions": 4}},
            {"entity_id": "top-target", "stat_line": {"receiving_targets": 10, "receiving_receptions": 5}},
        ]

        leader = identify_lead_receiver(team_games)

        assert leader["entity_id"] == "top-target"

    def test_ignores_players_without_receiving_targets(self):
        team_games = [{"entity_id": "rb", "stat_line": {"rushing_attempts": 15}}]

        assert identify_lead_receiver(team_games) is None

    def test_empty_team_games_returns_none(self):
        assert identify_lead_receiver([]) is None


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


class TestIdentifyTopReceivers:
    def test_returns_top_n_sorted_descending_by_targets(self):
        team_games = [
            {"entity_id": "wr3", "stat_line": {"receiving_targets": 5}},
            {"entity_id": "wr1", "stat_line": {"receiving_targets": 11}},
            {"entity_id": "wr2", "stat_line": {"receiving_targets": 8}},
            {"entity_id": "wr4", "stat_line": {"receiving_targets": 2}},
        ]

        top = identify_top_receivers(team_games, n=3)

        assert [row["entity_id"] for row in top] == ["wr1", "wr2", "wr3"]

    def test_fewer_candidates_than_n_returns_all_of_them(self):
        team_games = [{"entity_id": "wr1", "stat_line": {"receiving_targets": 4}}]

        assert len(identify_top_receivers(team_games, n=3)) == 1

    def test_ignores_players_without_receiving_targets(self):
        team_games = [{"entity_id": "rb", "stat_line": {"rushing_attempts": 15}}]

        assert identify_top_receivers(team_games, n=3) == []


class TestIdentifyTopRushers:
    def test_returns_top_n_sorted_descending_by_attempts(self):
        team_games = [
            {"entity_id": "rb2", "stat_line": {"rushing_attempts": 6}},
            {"entity_id": "rb1", "stat_line": {"rushing_attempts": 18}},
        ]

        top = identify_top_rushers(team_games, n=2)

        assert [row["entity_id"] for row in top] == ["rb1", "rb2"]


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


class TestBuildEventFeatures:
    def test_assembles_expected_fields(self):
        event = _event("E2", "2025-09-14", "KC", "LAC", 20, 17, week=2, season_type=2)
        elo_ratings = {"E2": {"home_pre_rating": 1550.0, "away_pre_rating": 1490.0}}
        home_history = [
            {"event_date": "2025-09-07", "participants": [
                {"entity_id": "KC", "result": {"score": 27}}, {"entity_id": "DET", "result": {"score": 20}},
            ]},
        ]
        away_history = []

        row = build_event_features(event, elo_ratings, home_history, away_history)

        assert row["event_key"] == "E2"
        assert row["week"] == 2
        assert row["season_type"] == 2
        assert row["home_elo"] == 1550.0
        assert row["away_elo"] == 1490.0
        assert row["elo_diff"] == 60.0
        assert row["home_rest_days"] == 7
        assert row["away_rest_days"] is None
        assert row["home_avg_points_scored"] == 27
        assert row["away_games_played"] == 0
        assert row["label_home_won"] is True
        assert row["label_home_score"] == 20
        assert row["label_away_score"] == 17

    def test_surfaces_week_and_season_type_from_the_event(self):
        # Distinct from the default-value case above -- confirms these
        # are actually read from the event dict, not coincidentally
        # matching _event()'s defaults.
        event = _event("E1", "2025-09-07", "KC", "LAC", week=15, season_type=3)

        row = build_event_features(event, {}, [], [])

        assert row["week"] == 15
        assert row["season_type"] == 3

    def test_missing_elo_entry_yields_none_diff(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["home_elo"] is None
        assert row["elo_diff"] is None

    def test_surfaces_venue_and_weather_from_the_event(self):
        event = _event(
            "E1", "2025-09-07", "KC", "LAC", 27, 20,
            venue_indoor=False, venue_city="Kansas City", venue_state="MO", weather_temperature=68,
        )

        row = build_event_features(event, {}, [], [])

        assert row["venue_indoor"] is False
        assert row["venue_city"] == "Kansas City"
        assert row["venue_state"] == "MO"
        assert row["weather_temperature"] == 68

    def test_venue_and_weather_default_to_none_when_absent_from_event(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)
        del event["venue_indoor"], event["venue_city"], event["venue_state"], event["weather_temperature"]

        row = build_event_features(event, {}, [], [])

        assert row["venue_indoor"] is None
        assert row["venue_city"] is None
        assert row["venue_state"] is None
        assert row["weather_temperature"] is None

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

    def test_divisional_game_and_travel_use_real_team_ids(self):
        # "12"/"13" are KC/LV's real ESPN team ids (both AFC West);
        # "KC"/"LAC"-style abbreviations used elsewhere in this test file
        # don't match library.features.nfl_teams' lookup tables, so this
        # test specifically uses the real ids to get a non-None result.
        event = _event("E1", "2025-09-07", "12", "13", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["is_divisional_game"] is True
        assert row["home_travel_km"] == 0
        assert row["away_travel_km"] > 0

    def test_non_divisional_game_is_false(self):
        # "12" (KC, AFC West) vs "9" (GB, NFC North).
        event = _event("E1", "2025-09-07", "12", "9", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["is_divisional_game"] is False

    def test_unknown_team_id_yields_none_not_a_crash(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["is_divisional_game"] is None
        assert row["home_travel_km"] is None
        assert row["away_travel_km"] is None

    def test_international_venue_gives_both_teams_travel(self):
        # "12"/"17" are KC/NE's real ids; the game is played in London,
        # so neither team is at their own market.
        event = _event("E1", "2025-09-07", "17", "12", 27, 20, venue_city="London")

        row = build_event_features(event, {}, [], [])

        assert row["is_international_game"] is True
        assert row["home_travel_km"] > 0
        assert row["away_travel_km"] > 0

    def test_unrecognized_non_us_venue_logs_a_warning(self, caplog):
        # A venue with no US state and no entry in INTERNATIONAL_VENUES
        # should surface a warning rather than silently mis-computing
        # travel distance for a new host city nobody's added yet.
        event = _event("E1", "2025-09-07", "17", "12", 27, 20, venue_city="Tokyo")
        event["venue_state"] = None

        with caplog.at_level("WARNING"):
            row = build_event_features(event, {}, [], [])

        assert row["is_international_game"] is False
        assert any("Tokyo" in r.message for r in caplog.records)

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

    def test_coach_fields_read_straight_off_the_event(self):
        event = _event(
            "E1", "2025-09-07", "KC", "LAC", 27, 20,
            home_coach_experience=27, away_coach_experience=1,
            home_coach_season_win_pct=0.7, away_coach_season_win_pct=0.4,
        )

        row = build_event_features(event, {}, [], [])

        assert row["home_coach_experience"] == 27
        assert row["away_coach_experience"] == 1
        assert row["home_coach_season_win_pct"] == 0.7
        assert row["away_coach_season_win_pct"] == 0.4

    def test_coach_fields_are_none_when_absent_from_event(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["home_coach_experience"] is None
        assert row["home_coach_season_win_pct"] is None

    def test_qb_injury_status_looks_up_the_presumptive_qb_by_entity_id(self):
        event = _event(
            "E1", "2025-09-07", "KC", "LAC", 27, 20,
            home_injuries=[{"entity_id": "mahomes", "status": "Questionable"}],
        )
        home_qb_games = [{"entity_id": "mahomes", "event_date": "2025-08-31", "stat_line": {}}]

        row = build_event_features(event, {}, [], [], home_qb_games=home_qb_games)

        assert row["home_qb_injury_status"] == 1
        assert row["away_qb_injury_status"] is None  # no away_qb_games given, no away_injuries either

    def test_team_injury_count_from_event_level_injuries(self):
        event = _event(
            "E1", "2025-09-07", "KC", "LAC", 27, 20,
            home_injuries=[
                {"entity_id": "1", "status": "Out"},
                {"entity_id": "2", "status": "Doubtful"},
                {"entity_id": "3", "status": "Questionable"},  # not counted
            ],
        )

        row = build_event_features(event, {}, [], [])

        assert row["home_team_injury_count"] == 2

    def test_injury_fields_are_none_not_zero_when_event_has_no_injuries_data(self):
        # Missing entirely (older event, or a fetch failure) must reach
        # training as a real missing value, not a false "definitely
        # healthy" zero -- see _injury_status_ordinal's own docstring.
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["home_qb_injury_status"] is None
        assert row["home_team_injury_count"] is None


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
        # ids test_nfl.py's other divisional/travel tests already use.
        player_game = self._player_game(team_id="13")
        event = _event("E1", "2025-09-07", "12", "13", 27, 20)
        elo_ratings = {}

        row = build_player_features(player_game, [], event, elo_ratings, None)

        assert row["is_divisional_game"] is True
        assert row["travel_km"] > 0  # "13" (LV) is the away side here
