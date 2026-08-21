"""
Unit tests for library.features.ncaambb -- build_event_features and
build_player_features. No AWS involved.

estimate_possessions/_efficiency_per_100 (the Dean Oliver math ncaambb.py
imports from library.features.common) have their own dedicated tests in
test_common_basketball.py, not duplicated here.
"""
import pytest

from library.features.ncaambb import build_event_features, build_player_features, is_conference_game

from _ncaambb_test_helpers import event as _event


class TestIsConferenceGame:
    def test_true_when_flag_is_true(self):
        assert is_conference_game(_event("E1", "2026-01-18", "150", "153", conference_competition=True)) is True

    def test_false_when_flag_is_false(self):
        assert is_conference_game(_event("E1", "2026-03-21", "150", "153", conference_competition=False)) is False

    def test_none_when_field_absent(self):
        assert is_conference_game(_event("E1", "2026-01-18", "150", "153")) is None


class TestBuildEventFeaturesCore:
    def test_assembles_expected_fields(self):
        event = _event("E1", "2025-12-01", "150", "153", 80, 75)
        elo_ratings = {"E1": {"home_pre_rating": 1600.0, "away_pre_rating": 1550.0}}

        row = build_event_features(event, elo_ratings, [], [])

        assert row["event_key"] == "E1"
        assert "week" not in row
        assert "season_type" not in row
        assert "home_travel_km" not in row
        assert "is_international_game" not in row
        assert row["home_elo"] == 1600.0
        assert row["away_elo"] == 1550.0
        assert row["elo_diff"] == 50.0
        assert row["label_home_won"] is True
        assert row["label_home_score"] == 80
        assert row["label_away_score"] == 75

    def test_missing_elo_entry_yields_none_diff(self):
        event = _event("E1", "2025-12-01", "150", "153", 80, 75)

        row = build_event_features(event, {}, [], [])

        assert row["home_elo"] is None
        assert row["elo_diff"] is None

    def test_surfaces_kickoff_hour_utc(self):
        event = _event("E1", "2025-12-01", "150", "153", kickoff_time="2025-12-01T23:30:00.000Z")

        row = build_event_features(event, {}, [], [])

        assert row["kickoff_hour_utc"] == 23

    def test_is_conference_game_flows_through_from_the_event(self):
        conference_event = _event("E1", "2026-01-18", "150", "153", conference_competition=True)
        row = build_event_features(conference_event, {}, [], [])
        assert row["is_conference_game"] is True

        nonconference_event = _event("E2", "2025-11-10", "150", "153", conference_competition=False)
        row2 = build_event_features(nonconference_event, {}, [], [])
        assert row2["is_conference_game"] is False


class TestBuildEventFeaturesInjuries:
    def test_team_injury_counts_from_home_and_away_injuries(self):
        event = _event("E1", "2025-12-01", "150", "153")
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
        event = _event("E1", "2025-12-01", "150", "153")

        row = build_event_features(event, {}, [], [])

        assert row["home_team_injury_count"] is None
        assert row["away_team_injury_count"] is None


class TestBuildEventFeaturesRollingBoxStats:
    def test_avg_rebounds_is_read_directly_not_derived(self):
        # Unlike NBA's own _total_rebounds workaround, NCAA MBB's box
        # score has a real raw "rebounds" stat_line key (confirmed live,
        # 2026-08-19) -- home_avg_rebounds should read it directly, not
        # sum offensive+defensive.
        event = _event("E2", "2025-12-08", "150", "153")
        home_box_history = [
            {"event_date": "2025-12-01", "stat_line": {
                "field_goals_made": 25, "field_goal_attempts": 55,
                "three_pointers_made": 8, "three_point_attempts": 20,
                "free_throws_made": 12, "free_throw_attempts": 15,
                "rebounds": 38, "offensive_rebounds": 9, "defensive_rebounds": 29,
                "assists": 14, "steals": 6, "blocks": 3, "turnovers": 11, "fouls": 16,
            }},
        ]

        row = build_event_features(event, {}, [], [], home_team_box_stats=home_box_history)

        assert row["home_avg_rebounds"] == 38
        assert row["home_avg_offensive_rebounds"] == 9
        assert row["home_avg_assists"] == 14
        assert row["home_avg_turnovers"] == 11
        assert row["home_field_goal_pct"] == 25 / 55
        assert row["home_three_point_pct"] == 8 / 20
        assert row["home_free_throw_pct"] == 12 / 15
        assert row["home_box_games_played"] == 1
        assert row["away_box_games_played"] == 0

    def test_offensive_and_defensive_efficiency_derived_from_possessions_and_scoring(self):
        event = _event("E2", "2025-12-08", "150", "153")
        home_team_events = [
            {"event_date": "2025-12-01", "participants": [
                {"entity_id": "150", "result": {"score": 80}},
                {"entity_id": "999", "result": {"score": 70}},
            ]},
        ]
        home_box_history = [
            {"event_date": "2025-12-01", "stat_line": {
                "field_goals_made": 25, "field_goal_attempts": 55,
                "three_pointers_made": 8, "three_point_attempts": 20,
                "free_throws_made": 12, "free_throw_attempts": 15,
                "offensive_rebounds": 9, "turnovers": 11,
            }},
        ]
        # possessions = 55 - 9 + 11 + 0.44*15 = 63.6
        row = build_event_features(
            event, {}, home_team_events, [], home_team_box_stats=home_box_history,
        )

        assert row["home_offensive_efficiency"] == pytest.approx(80 / 63.6 * 100)
        assert row["home_defensive_efficiency"] == pytest.approx(70 / 63.6 * 100)

    def test_efficiency_is_none_with_no_box_history(self):
        event = _event("E1", "2025-12-01", "150", "153")

        row = build_event_features(event, {}, [], [])

        assert row["home_offensive_efficiency"] is None
        assert row["home_defensive_efficiency"] is None


class TestBuildPlayerFeatures:
    def test_assembles_expected_fields_and_rolling_averages(self):
        event = _event("E1", "2025-12-08", "150", "153")
        player_game = {
            "event_key": "E1", "player_key": "ncaambb:player:1", "entity_id": "1",
            "team_id": "150", "event_date": "2025-12-08",
            "stat_line": {"points": 22}, "started": True,
        }
        prior_games = [
            {"event_date": "2025-12-01", "stat_line": {"points": 18}, "started": True},
        ]
        elo_ratings = {"E1": {"home_pre_rating": 1600.0, "away_pre_rating": 1550.0}}

        row = build_player_features(player_game, prior_games, event, elo_ratings, "2025-11-28")

        assert row["entity_id"] == "1"
        assert row["team_id"] == "150"
        assert row["opponent_id"] == "153"
        assert row["is_home"] is True
        assert row["avg_points"] == 18
        assert row["games_played"] == 1
        assert row["own_elo"] == 1600.0
        assert row["opponent_elo"] == 1550.0
        assert row["elo_diff"] == 50.0
        assert row["rest_days"] == 10
        assert row["label_stat_line"] == {"points": 22}
        assert row["label_started"] is True

    def test_away_team_player_gets_opponent_orientation(self):
        event = _event("E1", "2025-12-08", "150", "153")
        player_game = {
            "event_key": "E1", "player_key": "ncaambb:player:2", "entity_id": "2",
            "team_id": "153", "event_date": "2025-12-08", "stat_line": {}, "started": False,
        }

        row = build_player_features(player_game, [], event, {}, None)

        assert row["is_home"] is False
        assert row["opponent_id"] == "150"

    def test_is_conference_game_flows_through_from_the_event(self):
        event = _event("E1", "2026-01-18", "150", "153", conference_competition=True)
        player_game = {
            "event_key": "E1", "player_key": "ncaambb:player:1", "entity_id": "1",
            "team_id": "150", "event_date": "2026-01-18", "stat_line": {}, "started": True,
        }

        row = build_player_features(player_game, [], event, {}, None)

        assert row["is_conference_game"] is True
