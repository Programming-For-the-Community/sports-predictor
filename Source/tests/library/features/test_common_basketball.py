"""
Unit tests for library.features.common's basketball-specific math --
estimate_possessions/_efficiency_per_100 (Dean Oliver's standard
possessions estimate and points-per-100 efficiency). Sport-agnostic
basketball formulas, promoted here from library.features.nba 2026-08-20
when NCAA MBB's own feature module needed the identical formula -- see
project-ncaambb-onboarding memory. Both NBA's and NCAA MBB's own feature
modules import these rather than defining their own copy.
"""
import pytest

from library.features.common import _efficiency_per_100, estimate_possessions


class TestEstimatePossessions:
    def test_dean_oliver_formula(self):
        # FGA - OREB + TOV + 0.44*FTA = 90 - 10 + 14 + 0.44*20 = 102.8
        assert estimate_possessions(90, 10, 14, 20) == 102.8

    def test_none_if_any_input_missing(self):
        assert estimate_possessions(None, 10, 14, 20) is None
        assert estimate_possessions(90, None, 14, 20) is None
        assert estimate_possessions(90, 10, None, 20) is None
        assert estimate_possessions(90, 10, 14, None) is None


class TestEfficiencyPer100:
    def test_points_per_100_possessions(self):
        assert _efficiency_per_100(110.0, 100.0) == pytest.approx(110.0)
        assert _efficiency_per_100(55.0, 100.0) == pytest.approx(55.0)

    def test_none_when_points_or_possessions_missing_or_zero(self):
        assert _efficiency_per_100(None, 100.0) is None
        assert _efficiency_per_100(110.0, None) is None
        assert _efficiency_per_100(110.0, 0) is None
