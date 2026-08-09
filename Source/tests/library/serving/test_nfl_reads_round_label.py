"""
Unit tests for library.serving.nfl_reads._round_label -- postseason
week -> round-name mapping (Wild Card/Divisional/Conference Championship/
Super Bowl), None for regular season or an unmapped week. Split out of
what used to be one large test_nfl_reads.py -- see
test_nfl_reads_list_events.py's own history note.
"""
from library.serving import nfl_reads


class TestRoundLabel:
    def test_regular_season_is_none(self):
        assert nfl_reads._round_label({"season_type": 2, "week": 5}) is None

    def test_wild_card_is_week_1(self):
        assert nfl_reads._round_label({"season_type": 3, "week": 1}) == "Wild Card"

    def test_divisional_is_week_2(self):
        assert nfl_reads._round_label({"season_type": 3, "week": 2}) == "Divisional"

    def test_conference_championship_is_week_3(self):
        assert nfl_reads._round_label({"season_type": 3, "week": 3}) == "Conference Championship"

    def test_super_bowl_is_week_5(self):
        assert nfl_reads._round_label({"season_type": 3, "week": 5}) == "Super Bowl"

    def test_week_4_pro_bowl_has_no_label(self):
        # Always the Pro Bowl -- already excluded by is_real_franchise_matchup
        # before _round_label is ever consulted, so this documents "unmapped",
        # not an expected real code path.
        assert nfl_reads._round_label({"season_type": 3, "week": 4}) is None
