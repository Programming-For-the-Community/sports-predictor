"""
Unit tests for library.features.ncaafb.build_event_features -- core
fields, rolling-history fields, coach/rank fields, injury fields (always
None -- no data source exists), and the conference/bowl/playoff/travel
fields that replace NFL's static divisional table. No AWS involved.
"""
import pytest

from library.features.ncaafb import build_event_features

from _ncaafb_test_helpers import event as _event

_COORDS = {"333": (33.2083, -87.5504), "61": (33.9498, -83.3733)}  # Alabama, Georgia


class TestBuildEventFeaturesCore:
    def test_assembles_expected_fields(self):
        event = _event("E1", "2025-09-13", "333", "61", 27, 20, week=3, season_type="regular")
        elo_ratings = {"E1": {"home_pre_rating": 1600.0, "away_pre_rating": 1550.0}}

        row = build_event_features(event, elo_ratings, [], [], _COORDS)

        assert row["event_key"] == "E1"
        assert row["week"] == 3
        assert row["season_type"] == "regular"
        assert row["home_elo"] == 1600.0
        assert row["away_elo"] == 1550.0
        assert row["elo_diff"] == 50.0
        assert row["label_home_won"] is True
        assert row["label_home_score"] == 27
        assert row["label_away_score"] == 20

    def test_missing_elo_entry_yields_none_diff(self):
        event = _event("E1", "2025-09-13", "333", "61", 27, 20)

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["home_elo"] is None
        assert row["elo_diff"] is None

    def test_surfaces_kickoff_hour_utc(self):
        event = _event("E1", "2025-09-13", "333", "61", kickoff_time="2025-09-13T19:30:00.000Z")

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["kickoff_hour_utc"] == 19

    def test_venue_indoor_passed_through(self):
        event = _event("E1", "2025-09-13", "333", "61", venue_indoor=True)

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["venue_indoor"] is True


class TestBuildEventFeaturesRollingStats:
    def test_qb_rolling_stats_computed_from_qb_games(self):
        event = _event("E2", "2025-09-20", "333", "61")
        home_qb_games = [
            {"event_date": "2025-09-13", "stat_line": {"passing_yards": 280, "passing_touchdowns": 3}, "started": True},
        ]

        row = build_event_features(event, {}, [], [], _COORDS, home_qb_games=home_qb_games)

        assert row["home_qb_avg_passing_yards"] == 280
        assert row["home_qb_avg_passing_tds"] == 3
        assert row["home_qb_games_played"] == 1
        assert row["away_qb_games_played"] == 0

    def test_rb_and_wr_rolling_stats_computed_from_their_own_games(self):
        event = _event("E2", "2025-09-20", "333", "61")
        home_rb_games = [{"event_date": "2025-09-13", "stat_line": {"rushing_yards": 110}, "started": True}]
        home_wr_games = [{"event_date": "2025-09-13", "stat_line": {"receiving_yards": 90, "receiving_receptions": 5}, "started": True}]

        row = build_event_features(event, {}, [], [], _COORDS, home_rb_games=home_rb_games, home_wr_games=home_wr_games)

        assert row["home_rb_avg_rushing_yards"] == 110
        assert row["home_wr_avg_receiving_yards"] == 90
        assert row["home_wr_avg_receptions"] == 5

    def test_team_box_stats_computed_from_history_no_red_zone_field(self):
        # CFBD's team box score has no red-zone category (confirmed live)
        # -- unlike NFL, there's no home_red_zone_pct field at all here.
        event = _event("E2", "2025-09-20", "333", "61")
        home_box_history = [
            {"event_date": "2025-09-13", "stat_line": {
                "turnovers": 1, "total_yards": 400, "possession_time_seconds": 1900,
                "third_down_conversions": 7, "third_down_attempts": 14,
                "penalties": 4, "penalty_yards": 35,
            }},
        ]

        row = build_event_features(event, {}, [], [], _COORDS, home_team_box_stats=home_box_history)

        assert row["home_avg_turnovers"] == 1
        assert row["home_avg_total_yards"] == 400
        assert row["home_third_down_pct"] == 0.5
        assert "home_red_zone_pct" not in row

    def test_win_streak_reflects_team_history(self):
        event = _event("E3", "2025-09-27", "333", "61")
        home_history = [
            {"event_date": "2025-09-20", "participants": [
                {"entity_id": "333", "result": {"score": 27}}, {"entity_id": "9", "result": {"score": 20}},
            ]},
        ]

        row = build_event_features(event, {}, home_history, [], _COORDS)

        assert row["home_win_streak"] == 1


class TestBuildEventFeaturesCoachAndRank:
    def test_coach_and_rank_fields_read_straight_off_the_event(self):
        event = _event(
            "E1", "2025-09-13", "333", "61",
            home_coach_experience=5, home_coach_season_win_pct=0.8,
            home_current_rank=3, away_current_rank=None,
        )

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["home_coach_experience"] == 5
        assert row["home_coach_season_win_pct"] == 0.8
        assert row["home_current_rank"] == 3
        assert row["away_current_rank"] is None

    def test_no_career_playoff_win_pct_field_exists(self):
        event = _event("E1", "2025-09-13", "333", "61")

        row = build_event_features(event, {}, [], [], _COORDS)

        assert "home_coach_career_playoff_win_pct" not in row


class TestBuildEventFeaturesNoInjuryFields:
    def test_no_injury_columns_exist_at_all(self):
        # No injury data source exists for CFB -- unlike NFL, these
        # columns aren't feature-engineered at all here, not even as
        # permanently-null placeholders.
        event = _event("E1", "2025-09-13", "333", "61")

        row = build_event_features(event, {}, [], [], _COORDS)

        for key in ("home_qb_injury_status", "away_qb_injury_status", "home_team_injury_count", "away_team_injury_count"):
            assert key not in row


class TestBuildEventFeaturesConferenceBowlPlayoff:
    def test_conference_game_reads_cfbds_own_flag(self):
        event = _event("E1", "2025-09-13", "333", "61", conference_game=True)

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["is_conference_game"] is True

    def test_conference_game_none_when_cfbd_has_not_computed_it(self):
        event = _event("E1", "2025-09-13", "333", "61", conference_game=None)

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["is_conference_game"] is None

    def test_playoff_game_true_and_bowl_game_false_for_a_cfp_game(self):
        event = _event("E1", "2026-01-01", "333", "61", season_type="postseason", is_playoff_game=True)

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["is_playoff_game"] is True
        assert row["is_bowl_game"] is False

    def test_bowl_game_true_and_playoff_game_false_for_an_ordinary_bowl(self):
        event = _event("E1", "2025-12-20", "333", "61", season_type="postseason", is_playoff_game=False)

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["is_bowl_game"] is True
        assert row["is_playoff_game"] is False

    def test_regular_season_game_is_neither_bowl_nor_playoff(self):
        event = _event("E1", "2025-09-13", "333", "61", season_type="regular", is_playoff_game=False)

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["is_bowl_game"] is False
        assert row["is_playoff_game"] is False


class TestBuildEventFeaturesTravel:
    def test_travel_uses_dynamic_team_coordinates(self):
        event = _event("E1", "2025-09-13", "333", "61")

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["home_travel_km"] == 0
        assert row["away_travel_km"] > 0

    def test_unknown_team_id_yields_none_not_a_crash(self):
        event = _event("E1", "2025-09-13", "999", "888")

        row = build_event_features(event, {}, [], [], _COORDS)

        assert row["home_travel_km"] is None
        assert row["away_travel_km"] is None
