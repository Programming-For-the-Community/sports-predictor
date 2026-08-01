"""
Unit tests for library.features.nfl_teams' static reference data helpers.
"""
from library.features.nfl_teams import (
    TEAM_COORDINATES,
    TEAM_DIVISIONS,
    is_divisional_game,
    travel_distance_km,
)


class TestReferenceData:
    def test_every_team_has_both_a_division_and_coordinates(self):
        assert set(TEAM_DIVISIONS.keys()) == set(TEAM_COORDINATES.keys())

    def test_covers_all_32_teams(self):
        assert len(TEAM_DIVISIONS) == 32

    def test_each_division_has_exactly_four_teams(self):
        counts = {}
        for division in TEAM_DIVISIONS.values():
            counts[division] = counts.get(division, 0) + 1
        assert len(counts) == 8
        assert all(count == 4 for count in counts.values())


class TestIsDivisionalGame:
    def test_same_division_is_true(self):
        # KC (12) and LV (13) are both AFC West.
        assert is_divisional_game("12", "13") is True

    def test_different_division_is_false(self):
        # KC (12, AFC West) vs GB (9, NFC North).
        assert is_divisional_game("12", "9") is False

    def test_unknown_team_returns_none(self):
        assert is_divisional_game("12", "unknown-id") is None


class TestTravelDistanceKm:
    def test_same_market_is_zero(self):
        # LAC (24) and LAR (14) share a stadium/market.
        assert travel_distance_km("24", "14") == 0

    def test_cross_country_distance_is_substantial(self):
        # NE (17, Foxborough) traveling to SEA (26, Seattle) -- roughly
        # a cross-continent trip, should be several thousand km.
        distance = travel_distance_km("17", "26")
        assert distance > 3500

    def test_unknown_team_returns_none(self):
        assert travel_distance_km("unknown-id", "12") is None
