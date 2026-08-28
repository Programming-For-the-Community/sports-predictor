"""
Unit tests for library.features.pga_fedex_cup_points -- tier resolution
and the points-per-position table, including real PGA Tour tie-splitting.
"""
from library.features.pga_fedex_cup_points import (
    FEDEX_CUP_POINTS_BY_TIER,
    EVENT_TIER_OVERRIDES,
    points_for_field,
    tier_for_event,
)


class TestTierForEvent:
    def test_a_real_override_wins_over_is_major(self):
        # The Players isn't `is_major` on a stored PGA event (only the 4
        # majors set that flag) but is still real-table "major" tier.
        assert tier_for_event(2026, "THE PLAYERS Championship", is_major=False) == "major"

    def test_an_elevated_override_applies_even_though_is_major_is_false(self):
        assert tier_for_event(2026, "Memorial Tournament", is_major=False) == "elevated"

    def test_is_major_flag_used_when_no_override_matches(self):
        assert tier_for_event(2026, "Some Future Major Not Yet In The Table", is_major=True) == "major"

    def test_unlisted_non_major_event_fails_open_to_regular(self):
        assert tier_for_event(2026, "Totally Unlisted Weekly Event", is_major=False) == "regular"

    def test_none_tournament_name_falls_through_to_is_major_then_regular(self):
        assert tier_for_event(2026, None, is_major=False) == "regular"
        assert tier_for_event(2026, None, is_major=True) == "major"

    def test_a_season_with_no_overrides_at_all_still_fails_open(self):
        assert 1999 not in EVENT_TIER_OVERRIDES
        assert tier_for_event(1999, "The Sentry", is_major=False) == "regular"


class TestPointsForField:
    def test_exact_anchor_positions_pay_the_real_transcribed_value(self):
        result = points_for_field("regular", {"1": 1, "2": 2, "3": 3})
        assert result["1"] == 500
        assert result["2"] == 300
        assert result["3"] == 190

    def test_a_cut_or_withdrawn_golfer_with_no_finish_position_gets_zero(self):
        result = points_for_field("regular", {"1": 1, "2": None})
        assert result["2"] == 0.0

    def test_an_unrecognized_tier_falls_back_to_regular(self):
        assert points_for_field("nonsense_tier", {"1": 1}) == points_for_field("regular", {"1": 1})

    def test_interpolated_position_lands_between_its_two_nearest_anchors(self):
        # Position 12 is strictly between the real anchors at 10 (75) and 15 (55).
        result = points_for_field("regular", {"1": 12})
        assert 55 < result["1"] < 75

    def test_a_position_beyond_the_highest_anchor_clamps_rather_than_extrapolating(self):
        result = points_for_field("regular", {"1": 150})
        assert result["1"] == FEDEX_CUP_POINTS_BY_TIER["regular"][70]

    def test_two_golfers_tied_for_3rd_split_the_average_of_3rd_and_4th(self):
        # Real PGA Tour rule: a tie spans that many consecutive table
        # slots, split evenly -- not each golfer independently paid the
        # 3rd-place rate.
        result = points_for_field("regular", {"a": 3, "b": 3})
        expected = (190 + 135) / 2
        assert result["a"] == expected
        assert result["b"] == expected

    def test_a_three_way_tie_spans_three_consecutive_slots(self):
        result = points_for_field("regular", {"a": 1, "b": 1, "c": 1})
        expected = (500 + 300 + 190) / 3
        assert result["a"] == result["b"] == result["c"] == expected

    def test_playoff_tiers_pay_exactly_four_times_the_regular_rate(self):
        regular = points_for_field("regular", {"1": 1})["1"]
        assert points_for_field("fedex_st_jude", {"1": 1})["1"] == regular * 4
        assert points_for_field("bmw_championship", {"1": 1})["1"] == regular * 4

    def test_every_entity_id_gets_a_result_even_with_no_finish_at_all(self):
        result = points_for_field("regular", {"1": None, "2": None})
        assert result == {"1": 0.0, "2": 0.0}
