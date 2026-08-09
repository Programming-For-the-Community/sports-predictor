"""
Unit tests for library.features.nfl.build_event_features' coach fields
(experience/season_win_pct/career_playoff_win_pct, read straight off the
event -- see library/normalize/espn.py) and injury fields
(qb_injury_status/team_injury_count, both None-not-zero when an event has
no injuries data at all -- see _injury_status_ordinal's own docstring).
No AWS involved. Split out of what used to be one large test_nfl.py --
see test_nfl_event_features_core.py's own history note.
"""
from library.features.nfl import build_event_features

from _nfl_test_helpers import event as _event


class TestBuildEventFeaturesCoachAndInjury:
    def test_coach_fields_read_straight_off_the_event(self):
        event = _event(
            "E1", "2025-09-07", "KC", "LAC", 27, 20,
            home_coach_experience=27, away_coach_experience=1,
            home_coach_season_win_pct=0.7, away_coach_season_win_pct=0.4,
            home_coach_career_playoff_win_pct=0.625, away_coach_career_playoff_win_pct=None,
        )

        row = build_event_features(event, {}, [], [])

        assert row["home_coach_experience"] == 27
        assert row["away_coach_experience"] == 1
        assert row["home_coach_season_win_pct"] == 0.7
        assert row["away_coach_season_win_pct"] == 0.4
        assert row["home_coach_career_playoff_win_pct"] == 0.625
        assert row["away_coach_career_playoff_win_pct"] is None

    def test_coach_fields_are_none_when_absent_from_event(self):
        event = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)

        row = build_event_features(event, {}, [], [])

        assert row["home_coach_experience"] is None
        assert row["home_coach_career_playoff_win_pct"] is None
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
