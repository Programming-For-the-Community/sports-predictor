"""
Unit tests for library.features.nfl's pure feature-computation functions.
No AWS involved -- every function here takes already-fetched rows and
returns numbers, so these tests just hand-build the row shapes
FeatureStorage would return.
"""
import pytest

from library.features.nfl import (
    _mov_multiplier,
    build_event_features,
    build_player_features,
    compute_elo_ratings,
    identify_lead_receiver,
    identify_lead_rusher,
    identify_starting_qb,
    rest_days,
    rolling_player_stat_averages,
    rolling_team_scoring_averages,
)


def _event(
    event_key, event_date, home_id, away_id, home_score=None, away_score=None, week=1, season_type=2,
    venue_indoor=None, venue_city=None, venue_state=None, weather_temperature=None,
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
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }


class TestComputeEloRatings:
    def test_first_meeting_starts_both_teams_at_starting_rating(self):
        events = [_event("E1", "2025-09-07", "KC", "LAC", 27, 20)]

        ratings = compute_elo_ratings(events, starting_rating=1500)

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

        ratings = compute_elo_ratings(events, k_factor=20, home_advantage=55, starting_rating=1500)

        assert ratings["E2"]["home_pre_rating"] == pytest.approx(1517.10, abs=0.05)
        assert ratings["E2"]["away_pre_rating"] == pytest.approx(1482.90, abs=0.05)

    def test_bigger_margin_moves_ratings_more(self):
        # compute_elo_ratings only exposes PRE-game ratings, so the
        # movement from E1 is read via a follow-up event's pre-game rating.
        followup = _event("E2", "2025-09-14", "KC", "LAC", 20, 17)
        close_win = [_event("E1", "2025-09-07", "KC", "LAC", 24, 20), followup]
        blowout = [_event("E1", "2025-09-07", "KC", "LAC", 45, 3), followup]

        close_ratings = compute_elo_ratings(close_win, starting_rating=1500)
        blowout_ratings = compute_elo_ratings(blowout, starting_rating=1500)

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

        ratings = compute_elo_ratings(events, starting_rating=1500, home_advantage=10)

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

        ratings = compute_elo_ratings(events, starting_rating=1500)

        assert ratings["E2"]["home_pre_rating"] < 1500

    def test_processes_chronologically_regardless_of_input_order(self):
        earlier = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)
        later = _event("E2", "2025-09-14", "KC", "LAC", 20, 17)

        forward = compute_elo_ratings([earlier, later], starting_rating=1500)
        reversed_input = compute_elo_ratings([later, earlier], starting_rating=1500)

        assert forward["E2"]["home_pre_rating"] == reversed_input["E2"]["home_pre_rating"]

    def test_tie_moves_both_ratings_toward_each_other_evenly_when_equal(self):
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 20, 20),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        ratings = compute_elo_ratings(events, starting_rating=1500, home_advantage=0)

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

        ratings = compute_elo_ratings(events, starting_rating=1500)

        assert "BAD" not in ratings
        assert ratings["E1"]["home_pre_rating"] == 1500

    def test_event_missing_score_records_pre_rating_but_skips_update(self):
        scheduled = _event("E1", "2025-09-07", "KC", "LAC")  # no scores
        played = _event("E2", "2025-09-14", "KC", "LAC", 27, 20)

        ratings = compute_elo_ratings([scheduled, played], starting_rating=1500)

        assert ratings["E1"]["home_pre_rating"] == 1500
        # Unaffected by the scoreless event -- still starting_rating.
        assert ratings["E2"]["home_pre_rating"] == 1500


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
                },
            },
        ]

        row = build_event_features(event, {}, [], [], home_team_box_stats=home_box_history)

        assert row["home_avg_turnovers"] == 1
        assert row["home_avg_total_yards"] == 350
        assert row["home_avg_possession_time_seconds"] == 1800
        assert row["home_third_down_pct"] == 0.5
        assert row["home_red_zone_pct"] == 0.5
        assert row["home_box_games_played"] == 1
        assert row["away_box_games_played"] == 0

    def test_team_box_stats_default_to_none_when_no_history_given(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["home_avg_turnovers"] is None
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


class TestBuildPlayerFeatures:
    def test_assembles_expected_fields(self):
        player_game = {
            "event_key": "E2",
            "player_key": "PLAYER#mahomes-patrick",
            "entity_id": "mahomes-patrick",
            "team_id": "KC",
            "event_date": "2025-09-14",
            "stat_line": {"passing_yards": 310, "passing_tds": 3},
            "started": True,
        }
        prior_games = [
            {"event_date": "2025-09-07", "stat_line": {"passing_yards": 280}, "started": True},
        ]

        row = build_player_features(player_game, prior_games)

        assert row["entity_id"] == "mahomes-patrick"
        assert row["avg_passing_yards"] == 280
        assert row["games_played"] == 1
        assert row["label_stat_line"] == {"passing_yards": 310, "passing_tds": 3}
        assert row["label_started"] is True
