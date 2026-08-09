"""
Unit tests for live_features' depth-chart lookup and injury-filtering
helpers (_depth_chart_entries/_depth_chart_entry/_healthy_athlete_ids/
_healthy_athletes_across_slots) -- the pure functions
build_live_event_features/build_live_event_leader_candidates use to turn
a raw filtered depth chart plus an injuries list into ranked, healthy
candidate ids. No FeatureStorage involved -- these operate on plain dicts.
Split out of what used to be one large test_live_features.py -- see
test_live_features_event.py's own history note.
"""
import live_features


class TestDepthChartEntries:
    def test_finds_every_entry_by_abbreviation_regardless_of_outer_key(self):
        chart = {
            "wr1": {"position": {"abbreviation": "WR"}, "athletes": [{"id": "1"}]},
            "wr2": {"position": {"abbreviation": "WR"}, "athletes": [{"id": "2"}]},
            "rb": {"position": {"abbreviation": "RB"}, "athletes": [{"id": "3"}]},
        }
        assert live_features._depth_chart_entries(chart, "WR") == [chart["wr1"], chart["wr2"]]

    def test_empty_when_depth_chart_is_none(self):
        assert live_features._depth_chart_entries(None, "QB") == []

    def test_empty_when_no_matching_position(self):
        chart = {"rb": {"position": {"abbreviation": "RB"}, "athletes": []}}
        assert live_features._depth_chart_entries(chart, "QB") == []


class TestDepthChartEntry:
    def test_finds_entry_by_abbreviation_regardless_of_outer_key(self):
        chart = {"weird-key": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]}}
        assert live_features._depth_chart_entry(chart, "QB") == chart["weird-key"]

    def test_none_when_depth_chart_is_none(self):
        assert live_features._depth_chart_entry(None, "QB") is None

    def test_none_when_no_matching_position(self):
        chart = {"rb": {"position": {"abbreviation": "RB"}, "athletes": []}}
        assert live_features._depth_chart_entry(chart, "QB") is None

    def test_returns_the_first_entry_when_more_than_one_matches(self):
        # WR's real wr1/wr2/wr3 case -- "the" single leader means wr1
        # specifically, not an arbitrary one among the three.
        chart = {
            "wr1": {"position": {"abbreviation": "WR"}, "athletes": [{"id": "1"}]},
            "wr2": {"position": {"abbreviation": "WR"}, "athletes": [{"id": "2"}]},
        }
        assert live_features._depth_chart_entry(chart, "WR") == chart["wr1"]


class TestHealthyAthleteIds:
    def test_returns_all_ids_in_rank_order_when_no_injuries(self):
        entry = {"athletes": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
        assert live_features._healthy_athlete_ids(entry, None) == ["1", "2", "3"]

    def test_excludes_out_and_doubtful_keeps_questionable(self):
        entry = {"athletes": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
        injuries = [
            {"entity_id": "1", "status": "Out"},
            {"entity_id": "2", "status": "Questionable"},
        ]
        assert live_features._healthy_athlete_ids(entry, injuries) == ["2", "3"]

    def test_all_excluded_returns_empty_list(self):
        entry = {"athletes": [{"id": "1"}]}
        injuries = [{"entity_id": "1", "status": "Doubtful"}]
        assert live_features._healthy_athlete_ids(entry, injuries) == []


class TestHealthyAthletesAcrossSlots:
    def test_takes_each_slots_own_starter_before_any_slots_backup(self):
        # wr1's own backup ("1b") must NOT outrank wr2/wr3's starters --
        # round-robin by rank, not n-deep from whichever slot is first.
        entries = [
            {"athletes": [{"id": "1a"}, {"id": "1b"}]},
            {"athletes": [{"id": "2a"}]},
            {"athletes": [{"id": "3a"}]},
        ]
        assert live_features._healthy_athletes_across_slots(entries, None, 3) == ["1a", "2a", "3a"]

    def test_single_entry_behaves_like_taking_n_deep_from_one_slot(self):
        # QB/RB only ever pass one entry -- equivalent to the old
        # single-slot n-deep behavior.
        entries = [{"athletes": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}]
        assert live_features._healthy_athletes_across_slots(entries, None, 2) == ["1", "2"]

    def test_falls_through_to_a_slots_backup_when_fewer_slots_than_n(self):
        # Only one slot healthy-eligible but n=2 requested -- fills the
        # remainder from that slot's own next-ranked athlete rather than
        # stopping short.
        entries = [
            {"athletes": [{"id": "1a"}, {"id": "1b"}]},
            {"athletes": [{"id": "2a"}]},
        ]
        assert live_features._healthy_athletes_across_slots(entries, None, 3) == ["1a", "2a", "1b"]

    def test_excludes_injured_athletes_per_slot(self):
        entries = [{"athletes": [{"id": "1a"}]}, {"athletes": [{"id": "2a"}]}]
        injuries = [{"entity_id": "1a", "status": "Out"}]
        assert live_features._healthy_athletes_across_slots(entries, injuries, 2) == ["2a"]

    def test_empty_entries_returns_empty_list(self):
        assert live_features._healthy_athletes_across_slots([], None, 3) == []
