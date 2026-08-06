"""
Unit tests for library.features.nfl's pure feature-computation functions --
the NFL-specific half (leader identification, event/player feature
assembly). The sport-agnostic half these build on (Elo, rolling averages,
streaks, injuries) is tested in test_common.py. No AWS involved -- every
function here takes already-fetched rows and returns numbers, so these
tests just hand-build the row shapes FeatureStorage would return.
"""
import pytest

from library.features.nfl import (
    build_event_features,
    build_player_features,
    identify_lead_receiver,
    identify_lead_rusher,
    identify_starting_qb,
    identify_top_receivers,
    identify_top_rushers,
)


def _event(
    event_key, event_date, home_id, away_id, home_score=None, away_score=None, week=1, season_type=2,
    venue_indoor=None, venue_city=None, venue_state=None, weather_temperature=None,
    home_coach_experience=None, away_coach_experience=None,
    home_coach_season_win_pct=None, away_coach_season_win_pct=None,
    home_injuries=None, away_injuries=None, kickoff_time=None,
):
    home_result = {"score": home_score, "won": home_score is not None and home_score > away_score}
    away_result = {"score": away_score, "won": away_score is not None and away_score > home_score}
    return {
        "event_key": event_key,
        "event_date": event_date,
        "week": week,
        "season_type": season_type,
        "kickoff_time": kickoff_time,
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

    def test_surfaces_kickoff_hour_utc_from_kickoff_time(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20, kickoff_time="2025-09-07T20:25Z")

        row = build_event_features(event, {}, [], [])

        assert row["kickoff_hour_utc"] == 20

    def test_kickoff_hour_utc_is_none_when_kickoff_time_missing(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["kickoff_hour_utc"] is None

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
        # ids test_nfl.py's other divisional/travel tests already use.
        player_game = self._player_game(team_id="13")
        event = _event("E1", "2025-09-07", "12", "13", 27, 20)
        elo_ratings = {}

        row = build_player_features(player_game, [], event, elo_ratings, None)

        assert row["is_divisional_game"] is True
        assert row["travel_km"] > 0  # "13" (LV) is the away side here
