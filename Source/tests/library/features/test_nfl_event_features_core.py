"""
Unit tests for library.features.nfl.build_event_features' core fields:
event_key/week/season_type/Elo/rest_days/avg_points/labels, plus
kickoff_hour_utc and venue/weather passthrough. The sport-agnostic half
these build on (Elo, rolling averages, streaks, injuries) is tested in
test_common.py. No AWS involved. Split out of what used to be one large
test_nfl.py -- see test_nfl_leaders.py, test_nfl_event_features_rolling_
stats.py, test_nfl_event_features_travel.py, test_nfl_event_features_
coach_injury.py, and test_nfl_player_features.py for this file's siblings,
one per concern. Shared fixture lives in _nfl_test_helpers.py (not
test_-prefixed, so pytest never collects it as a test module itself).
"""
from library.features.nfl import build_event_features

from _nfl_test_helpers import event as _event


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
