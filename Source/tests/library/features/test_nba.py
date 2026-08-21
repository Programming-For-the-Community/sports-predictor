"""
Unit tests for library.features.nba -- build_event_features and
build_player_features. No AWS involved. Uses real NBA team ids from
library.features.nba_teams (BOS=2, LAL=13) for the travel/division-adjacent
assertions, same convention test_ncaafb_event_features.py uses for CFBD ids.

estimate_possessions/_efficiency_per_100 (the Dean Oliver math nba.py
re-exports from library.features.common) have their own dedicated tests
in test_common_basketball.py now, not duplicated here.
"""
import pytest

from library.features.nba import build_event_features, build_player_features

from _nba_test_helpers import event as _event


class TestBuildEventFeaturesCore:
    def test_assembles_expected_fields(self):
        event = _event("E1", "2025-12-01", "2", "13", 110, 105)
        elo_ratings = {"E1": {"home_pre_rating": 1600.0, "away_pre_rating": 1550.0}}

        row = build_event_features(event, elo_ratings, [], [])

        assert row["event_key"] == "E1"
        assert "week" not in row
        assert "season_type" not in row
        assert row["home_elo"] == 1600.0
        assert row["away_elo"] == 1550.0
        assert row["elo_diff"] == 50.0
        assert row["label_home_won"] is True
        assert row["label_home_score"] == 110
        assert row["label_away_score"] == 105

    def test_missing_elo_entry_yields_none_diff(self):
        event = _event("E1", "2025-12-01", "2", "13", 110, 105)

        row = build_event_features(event, {}, [], [])

        assert row["home_elo"] is None
        assert row["elo_diff"] is None

    def test_surfaces_kickoff_hour_utc(self):
        event = _event("E1", "2025-12-01", "2", "13", kickoff_time="2025-12-01T23:30:00.000Z")

        row = build_event_features(event, {}, [], [])

        assert row["kickoff_hour_utc"] == 23

    def test_divisional_and_international_flags(self):
        # BOS/NY (18) are both Atlantic; venue_city None -- not international.
        divisional_event = _event("E1", "2025-12-01", "2", "18")
        row = build_event_features(divisional_event, {}, [], [])
        assert row["is_divisional_game"] is True
        assert row["is_international_game"] is False

        mexico_city_event = _event("E2", "2025-12-01", "2", "13", venue_city="Mexico City")
        row2 = build_event_features(mexico_city_event, {}, [], [])
        assert row2["is_international_game"] is True

    def test_home_team_travels_zero_away_team_travels_real_distance(self):
        event = _event("E1", "2025-12-01", "2", "13")  # BOS hosts LAL

        row = build_event_features(event, {}, [], [])

        assert row["home_travel_km"] == 0
        assert row["away_travel_km"] > 3000


class TestBuildEventFeaturesInjuries:
    def test_team_injury_counts_from_home_and_away_injuries(self):
        event = _event("E1", "2025-12-01", "2", "13")
        event["home_injuries"] = [{"entity_id": "1", "status": "Out"}, {"entity_id": "2", "status": "Questionable"}]
        event["away_injuries"] = [{"entity_id": "3", "status": "Doubtful"}]

        row = build_event_features(event, {}, [], [])

        # Questionable isn't counted -- same threshold _team_injury_count
        # (library.features.common) already applies for every sport.
        assert row["home_team_injury_count"] == 1
        assert row["away_team_injury_count"] == 1

    def test_no_injuries_data_on_event_yields_none_not_zero(self):
        # Forward-only -- an event an ingest run never enriched (e.g. a
        # historical backfilled row) has no injuries key at all, a real
        # missing value, not "confirmed healthy".
        event = _event("E1", "2025-12-01", "2", "13")

        row = build_event_features(event, {}, [], [])

        assert row["home_team_injury_count"] is None
        assert row["away_team_injury_count"] is None


class TestBuildEventFeaturesRollingBoxStats:
    def test_team_box_stats_computed_from_history(self):
        event = _event("E2", "2025-12-08", "2", "13")
        home_box_history = [
            {"event_date": "2025-12-01", "stat_line": {
                "field_goals_made": 42, "field_goal_attempts": 90,
                "three_pointers_made": 14, "three_point_attempts": 38,
                "free_throws_made": 18, "free_throw_attempts": 22,
                "offensive_rebounds": 10, "defensive_rebounds": 35,
                "assists": 26, "steals": 8, "blocks": 5, "turnovers": 13, "fouls": 19,
            }},
        ]

        row = build_event_features(event, {}, [], [], home_team_box_stats=home_box_history)

        # No raw "rebounds" stat_line key -- ESPN's team boxscore only
        # exposes offensive/defensive rebounds separately (see
        # library.features.nba._total_rebounds); derived as their sum.
        assert row["home_avg_rebounds"] == 45
        assert row["home_avg_offensive_rebounds"] == 10
        assert row["home_avg_assists"] == 26
        assert row["home_avg_turnovers"] == 13
        assert row["home_field_goal_pct"] == 42 / 90
        assert row["home_three_point_pct"] == 14 / 38
        assert row["home_free_throw_pct"] == 18 / 22
        assert row["home_box_games_played"] == 1
        assert row["away_box_games_played"] == 0

    def test_offensive_and_defensive_efficiency_derived_from_possessions_and_scoring(self):
        event = _event("E2", "2025-12-08", "2", "13")
        home_team_events = [
            {"event_date": "2025-12-01", "participants": [
                {"entity_id": "2", "result": {"score": 110}},
                {"entity_id": "99", "result": {"score": 100}},
            ]},
        ]
        home_box_history = [
            {"event_date": "2025-12-01", "stat_line": {
                # made counterparts included -- _rate (library.features.common)
                # indexes the numerator directly, same assumption normalize
                # always satisfies since both halves come from the same
                # compound-key split (see Source/aws-lambdas/nba/normalize/
                # handler.py's _COMPOUND_KEY_SPLITS).
                "field_goals_made": 42, "field_goal_attempts": 90,
                "three_pointers_made": 14, "three_point_attempts": 38,
                "free_throws_made": 18, "free_throw_attempts": 20,
                "offensive_rebounds": 10, "turnovers": 14,
            }},
        ]
        # possessions = 90 - 10 + 14 + 0.44*20 = 102.8
        # offensive_efficiency = 110 / 102.8 * 100 ~= 106.9979...
        # defensive_efficiency = 100 / 102.8 * 100 ~= 97.2762...

        row = build_event_features(
            event, {}, home_team_events, [], home_team_box_stats=home_box_history,
        )

        assert row["home_offensive_efficiency"] == pytest.approx(110 / 102.8 * 100)
        assert row["home_defensive_efficiency"] == pytest.approx(100 / 102.8 * 100)

    def test_efficiency_is_none_with_no_box_history(self):
        event = _event("E1", "2025-12-01", "2", "13")

        row = build_event_features(event, {}, [], [])

        assert row["home_offensive_efficiency"] is None
        assert row["home_defensive_efficiency"] is None


class TestBuildPlayerFeatures:
    def test_assembles_expected_fields_and_rolling_averages(self):
        event = _event("E1", "2025-12-08", "2", "13")
        player_game = {
            "event_key": "E1", "player_key": "nba:player:1", "entity_id": "1",
            "team_id": "2", "event_date": "2025-12-08",
            "stat_line": {"points": 30}, "started": True,
        }
        prior_games = [
            {"event_date": "2025-12-01", "stat_line": {"points": 24}, "started": True},
        ]
        elo_ratings = {"E1": {"home_pre_rating": 1600.0, "away_pre_rating": 1550.0}}

        row = build_player_features(player_game, prior_games, event, elo_ratings, "2025-11-28")

        assert row["entity_id"] == "1"
        assert row["team_id"] == "2"
        assert row["opponent_id"] == "13"
        assert row["is_home"] is True
        assert row["avg_points"] == 24
        assert row["games_played"] == 1
        assert row["own_elo"] == 1600.0
        assert row["opponent_elo"] == 1550.0
        assert row["elo_diff"] == 50.0
        assert row["rest_days"] == 10
        assert row["label_stat_line"] == {"points": 30}
        assert row["label_started"] is True

    def test_away_team_player_gets_opponent_orientation(self):
        event = _event("E1", "2025-12-08", "2", "13")
        player_game = {
            "event_key": "E1", "player_key": "nba:player:2", "entity_id": "2",
            "team_id": "13", "event_date": "2025-12-08", "stat_line": {}, "started": False,
        }

        row = build_player_features(player_game, [], event, {}, None)

        assert row["is_home"] is False
        assert row["opponent_id"] == "2"
        assert row["travel_km"] > 3000  # away team's own travel, not 0
