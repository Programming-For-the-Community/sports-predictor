"""
Unit tests for library.features.nfl_teams' static reference data helpers.
"""
from library.features.nfl_teams import (
    INTERNATIONAL_VENUES,
    TEAM_COORDINATES,
    TEAM_DIVISIONS,
    is_divisional_game,
    is_international_game,
    travel_distances_km,
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


class TestIsInternationalGame:
    def test_known_host_city_is_true(self):
        assert is_international_game("London") is True

    def test_domestic_city_is_false(self):
        assert is_international_game("Kansas City") is False

    def test_none_is_false(self):
        assert is_international_game(None) is False


class TestTravelDistancesKm:
    def test_same_market_is_zero(self):
        # LAC (24) and LAR (14) share a stadium/market.
        home, away = travel_distances_km("24", "14", venue_city=None)
        assert home == 0
        assert away == 0

    def test_ordinary_game_home_team_travels_zero(self):
        # NE (17, Foxborough) at SEA (26, Seattle) -- an ordinary game,
        # not one of INTERNATIONAL_VENUES.
        home, away = travel_distances_km("17", "26", venue_city="Seattle")
        assert home == 0
        assert away > 3500  # roughly a cross-continent trip

    def test_international_venue_gives_both_teams_real_travel(self):
        # KC (12) at NE (17), played in London -- neither team is at
        # their own market, so both get a nonzero distance to Wembley.
        home, away = travel_distances_km("17", "12", venue_city="London")
        assert home > 0
        assert away > 0

    def test_unknown_team_returns_none_none(self):
        assert travel_distances_km("unknown-id", "12", venue_city=None) == (None, None)
