"""
Unit tests for library.features.nfl.build_event_features' travel/
divisional/international-game fields (is_divisional_game/home_travel_km/
away_travel_km/is_international_game), backed by
library.features.nfl_teams' real-team-id lookup tables. No AWS involved.
Split out of what used to be one large test_nfl.py -- see
test_nfl_event_features_core.py's own history note.
"""
from library.features.nfl import build_event_features

from _nfl_test_helpers import event as _event


class TestBuildEventFeaturesTravel:
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
