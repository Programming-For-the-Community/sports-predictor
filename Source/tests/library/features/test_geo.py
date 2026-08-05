"""
Unit tests for library.features.geo's sport-agnostic divisional/travel
mechanism, using synthetic data dicts rather than real NFL teams --
library.features.nfl_teams's own tests (test_nfl_teams.py) cover the NFL
data binding specifically, using real team ids.
"""
from library.features import geo

TEAM_DIVISIONS = {"A": "East", "B": "East", "C": "West"}
TEAM_COORDINATES = {"A": (40.0, -75.0), "B": (41.0, -74.0), "C": (34.0, -118.0)}
INTERNATIONAL_VENUES = {"Neutral City": (51.5, -0.1)}


class TestIsDivisionalGame:
    def test_same_division_is_true(self):
        assert geo.is_divisional_game("A", "B", TEAM_DIVISIONS) is True

    def test_different_division_is_false(self):
        assert geo.is_divisional_game("A", "C", TEAM_DIVISIONS) is False

    def test_unknown_team_returns_none(self):
        assert geo.is_divisional_game("A", "unknown-id", TEAM_DIVISIONS) is None


class TestIsInternationalGame:
    def test_known_host_city_is_true(self):
        assert geo.is_international_game("Neutral City", INTERNATIONAL_VENUES) is True

    def test_domestic_city_is_false(self):
        assert geo.is_international_game("Somewhere Else", INTERNATIONAL_VENUES) is False

    def test_none_is_false(self):
        assert geo.is_international_game(None, INTERNATIONAL_VENUES) is False


class TestTravelDistancesKm:
    def test_same_coordinates_is_zero(self):
        home, away = geo.travel_distances_km("A", "A", None, TEAM_COORDINATES, INTERNATIONAL_VENUES)
        assert home == 0
        assert away == 0

    def test_ordinary_game_home_team_travels_zero(self):
        home, away = geo.travel_distances_km("C", "A", "Some City", TEAM_COORDINATES, INTERNATIONAL_VENUES)
        assert home == 0
        assert away > 0

    def test_international_venue_gives_both_teams_real_travel(self):
        home, away = geo.travel_distances_km("A", "B", "Neutral City", TEAM_COORDINATES, INTERNATIONAL_VENUES)
        assert home > 0
        assert away > 0

    def test_unknown_team_returns_none_none(self):
        result = geo.travel_distances_km("unknown-id", "A", None, TEAM_COORDINATES, INTERNATIONAL_VENUES)
        assert result == (None, None)


class TestHaversineKm:
    def test_same_point_is_zero(self):
        assert geo.haversine_km((40.0, -75.0), (40.0, -75.0)) == 0

    def test_symmetric(self):
        a, b = (40.0, -75.0), (34.0, -118.0)
        assert geo.haversine_km(a, b) == geo.haversine_km(b, a)
