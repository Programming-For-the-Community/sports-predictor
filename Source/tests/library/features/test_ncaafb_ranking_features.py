"""
Unit tests for library.features.ncaafb.build_team_week_features -- the
team-week granularity feature row for the National Ranking model, a new
model shape distinct from build_event_features/build_player_features (see
that function's own docstring).
"""
from library.features.ncaafb import build_team_week_features

from _ncaafb_test_helpers import event as _event


class TestBuildTeamWeekFeatures:
    def test_assembles_expected_fields_for_the_home_side(self):
        event = _event(
            "E3", "2025-09-27", "333", "61", 30, 10, week=4,
            home_conference="SEC", home_current_rank=5,
        )
        elo_ratings = {"E3": {"home_pre_rating": 1620.0, "away_pre_rating": 1580.0}}

        row = build_team_week_features("333", event, elo_ratings, [])

        assert row["team_id"] == "333"
        assert row["week"] == 4
        assert row["conference"] == "SEC"
        assert row["elo"] == 1620.0
        assert row["label_current_rank"] == 5
        assert row["games_played"] == 0
        assert row["wins"] == 0
        assert row["losses"] == 0

    def test_away_side_reads_away_conference_elo_and_rank(self):
        event = _event(
            "E3", "2025-09-27", "333", "61", 30, 10, week=4,
            away_conference="Big Ten", away_current_rank=12,
        )
        elo_ratings = {"E3": {"home_pre_rating": 1620.0, "away_pre_rating": 1580.0}}

        row = build_team_week_features("61", event, elo_ratings, [])

        assert row["conference"] == "Big Ten"
        assert row["elo"] == 1580.0
        assert row["label_current_rank"] == 12

    def test_label_is_none_when_team_is_unranked(self):
        event = _event("E3", "2025-09-27", "333", "61", 30, 10, home_current_rank=None)

        row = build_team_week_features("333", event, {}, [])

        assert row["label_current_rank"] is None

    def test_season_record_and_scoring_computed_from_prior_games(self):
        team_season_events = [
            {"event_date": "2025-09-20", "participants": [
                {"entity_id": "333", "result": {"score": 28}}, {"entity_id": "9", "result": {"score": 14}},
            ]},
            {"event_date": "2025-09-13", "participants": [
                {"entity_id": "333", "result": {"score": 10}}, {"entity_id": "10", "result": {"score": 24}},
            ]},
        ]
        event = _event("E3", "2025-09-27", "333", "61")

        row = build_team_week_features("333", event, {}, team_season_events)

        assert row["wins"] == 1
        assert row["losses"] == 1
        assert row["games_played"] == 2
        assert row["avg_points_scored"] == 19  # (28 + 10) / 2
        assert row["win_streak"] == 1  # most recent game (index 0) was a win

    def test_strength_of_schedule_averages_opponent_pre_game_elo(self):
        team_season_events = [
            {"event_key": "E1", "event_date": "2025-09-13", "participants": [
                {"entity_id": "333", "role": "home", "result": {"score": 10}},
                {"entity_id": "10", "role": "away", "result": {"score": 24}},
            ]},
        ]
        elo_ratings = {"E1": {"home_pre_rating": 1600.0, "away_pre_rating": 1500.0}}
        event = _event("E3", "2025-09-27", "333", "61")

        row = build_team_week_features("333", event, elo_ratings, team_season_events)

        assert row["strength_of_schedule"] == 1500.0  # opponent "10"'s own pre-game rating

    def test_strength_of_schedule_none_with_no_season_history(self):
        event = _event("E1", "2025-09-13", "333", "61")

        row = build_team_week_features("333", event, {}, [])

        assert row["strength_of_schedule"] is None
