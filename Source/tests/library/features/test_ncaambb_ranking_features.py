"""
Unit tests for library.features.ncaambb.build_team_week_features -- the
team-poll granularity feature row for the National Ranking model.

Poll-centric, not event-centric, unlike NCAAFB's own
build_team_week_features -- see that function's own docstring for why.
own_elo/current_rank are passed in directly (pre-resolved by the caller)
rather than looked up from an event dict.
"""
from library.features.ncaambb import build_team_week_features


class TestBuildTeamWeekFeatures:
    def test_assembles_expected_fields(self):
        row = build_team_week_features("150", "2026-01-19", 2026, 1620.0, [], {}, current_rank=5)

        assert row["team_id"] == "150"
        assert row["as_of_date"] == "2026-01-19"
        assert row["season"] == 2026
        assert row["elo"] == 1620.0
        assert row["label_current_rank"] == 5
        assert row["games_played"] == 0
        assert row["wins"] == 0
        assert row["losses"] == 0

    def test_label_is_none_when_team_is_unranked(self):
        row = build_team_week_features("150", "2026-01-19", 2026, 1500.0, [], {}, current_rank=None)

        assert row["label_current_rank"] is None

    def test_season_record_and_scoring_computed_from_prior_games(self):
        team_season_events = [
            {"event_date": "2026-01-12", "participants": [
                {"entity_id": "150", "result": {"score": 78}}, {"entity_id": "9", "result": {"score": 65}},
            ]},
            {"event_date": "2026-01-05", "participants": [
                {"entity_id": "150", "result": {"score": 60}}, {"entity_id": "10", "result": {"score": 70}},
            ]},
        ]

        row = build_team_week_features("150", "2026-01-19", 2026, None, team_season_events, {}, None)

        assert row["wins"] == 1
        assert row["losses"] == 1
        assert row["games_played"] == 2
        assert row["avg_points_scored"] == 69  # (78 + 60) / 2
        assert row["win_streak"] == 1  # most recent game (index 0) was a win

    def test_strength_of_schedule_averages_opponent_pre_game_elo(self):
        team_season_events = [
            {"event_key": "E1", "event_date": "2026-01-05", "participants": [
                {"entity_id": "150", "role": "home", "result": {"score": 60}},
                {"entity_id": "10", "role": "away", "result": {"score": 70}},
            ]},
        ]
        elo_ratings = {"E1": {"home_pre_rating": 1600.0, "away_pre_rating": 1500.0}}

        row = build_team_week_features("150", "2026-01-19", 2026, None, team_season_events, elo_ratings, None)

        assert row["strength_of_schedule"] == 1500.0  # opponent "10"'s own pre-game rating

    def test_strength_of_schedule_none_with_no_season_history(self):
        row = build_team_week_features("150", "2026-01-19", 2026, None, [], {}, None)

        assert row["strength_of_schedule"] is None
