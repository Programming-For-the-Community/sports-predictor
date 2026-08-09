"""
Unit tests for live_features' roster-freshness/eligibility helpers
(_is_roster_entry_fresh/_eligible_entity_ids) and the _LEADER_POSITIONS
position-pool mapping (which positions compete for which leader
category's candidate slots -- WR pulls in TE/RB, RB pulls in FB/a
scrambling QB). No FeatureStorage involved -- these operate on plain
dicts. Split out of what used to be one large test_live_features.py --
see test_live_features_event.py's own history note.
"""
from datetime import date

import live_features


def _roster_entry(entity_id, position, as_of=None):
    # as_of defaults to real today -- "fresh" per _is_roster_entry_fresh,
    # which (via _fresh_roster's own default) judges freshness against
    # today, not the event's own date. Tests exercising staleness
    # override it with something clearly in the past.
    return {"entity_id": entity_id, "metadata": {"team_id_as_of": as_of or date.today().isoformat(), "position": position}}


class TestIsRosterEntryFresh:
    def test_same_day_is_fresh(self):
        entity = {"metadata": {"team_id_as_of": "2025-09-21"}}
        assert live_features._is_roster_entry_fresh(entity, "2025-09-21") is True

    def test_within_staleness_window_is_fresh(self):
        entity = {"metadata": {"team_id_as_of": "2025-09-10"}}
        assert live_features._is_roster_entry_fresh(entity, "2025-09-21") is True  # 11 days

    def test_beyond_staleness_window_is_not_fresh(self):
        entity = {"metadata": {"team_id_as_of": "2025-08-01"}}
        assert live_features._is_roster_entry_fresh(entity, "2025-09-21") is False  # 51 days

    def test_years_stale_is_not_fresh(self):
        # The exact reported bug: a retired player's roster entry frozen
        # years in the past.
        entity = {"metadata": {"team_id_as_of": "2020-01-01"}}
        assert live_features._is_roster_entry_fresh(entity, "2025-09-21") is False

    def test_missing_team_id_as_of_is_not_fresh(self):
        assert live_features._is_roster_entry_fresh({"metadata": {}}, "2025-09-21") is False

    def test_malformed_date_is_not_fresh(self):
        entity = {"metadata": {"team_id_as_of": "not-a-date"}}
        assert live_features._is_roster_entry_fresh(entity, "2025-09-21") is False


class TestEligibleEntityIds:
    def test_filters_to_matching_positions(self):
        roster = [_roster_entry("1", "QB"), _roster_entry("2", "WR"), _roster_entry("3", "RB")]
        assert live_features._eligible_entity_ids(roster, {"QB"}) == ["1"]

    def test_matches_any_position_in_the_set(self):
        roster = [_roster_entry("1", "WR"), _roster_entry("2", "TE"), _roster_entry("3", "RB")]
        assert set(live_features._eligible_entity_ids(roster, {"WR", "TE", "RB"})) == {"1", "2", "3"}

    def test_missing_position_metadata_is_excluded(self):
        roster = [{"entity_id": "1", "metadata": {}}]
        assert live_features._eligible_entity_ids(roster, {"QB"}) == []


class TestLeaderPositions:
    def test_qb_slot_includes_only_qb(self):
        assert live_features._LEADER_POSITIONS["QB"] == {"QB"}

    def test_rb_slot_includes_fb_and_scrambling_qb(self):
        assert live_features._LEADER_POSITIONS["RB"] == {"RB", "FB", "QB"}

    def test_wr_slot_includes_te_and_receiving_rb(self):
        assert live_features._LEADER_POSITIONS["WR"] == {"WR", "TE", "RB"}
