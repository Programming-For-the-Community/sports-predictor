"""
library.features.nhl_teams: the static division/coordinate/time-zone
tables and the season-aware alignment lookups.
"""
from collections import Counter

from library.features import nhl_teams

CURRENT_TEAM_IDS = set(nhl_teams.TEAM_DIVISIONS) - {nhl_teams.ARIZONA_TEAM_ID}


class TestTables:
    def test_32_current_teams_in_four_divisions_of_eight(self):
        assert len(CURRENT_TEAM_IDS) == 32
        counts = Counter(nhl_teams.TEAM_DIVISIONS[team_id] for team_id in CURRENT_TEAM_IDS)
        assert set(counts.values()) == {8}
        assert len(counts) == 4

    def test_every_team_has_coordinates_and_a_utc_offset(self):
        assert set(nhl_teams.TEAM_COORDINATES) == set(nhl_teams.TEAM_DIVISIONS)
        assert set(nhl_teams.TEAM_UTC_OFFSETS) == set(nhl_teams.TEAM_DIVISIONS)

    def test_2021_temporary_divisions_cover_the_31_teams_of_that_season(self):
        divisions = nhl_teams._TEAM_DIVISIONS_2021
        assert len(divisions) == 31
        assert "124292" not in divisions  # Seattle joined for 2022
        assert Counter(divisions.values()) == {"2021 North": 7, "2021 East": 8, "2021 Central": 8, "2021 West": 8}


class TestFranchise:
    def test_arizona_maps_to_utah_and_everyone_else_to_themselves(self):
        assert nhl_teams.franchise_id("24") == "129764"
        assert nhl_teams.franchise_id("129764") == "129764"
        assert nhl_teams.franchise_id("13") == "13"

    def test_is_real_franchise_matchup(self):
        real = {"participants": [{"entity_id": "13"}, {"entity_id": "24"}]}
        all_star = {"participants": [{"entity_id": "129030"}, {"entity_id": "129031"}]}

        assert nhl_teams.is_real_franchise_matchup(real)
        assert not nhl_teams.is_real_franchise_matchup(all_star)
        assert not nhl_teams.is_franchise_team(None)


class TestDivisionalAndConference:
    def test_current_alignment(self):
        assert nhl_teams.is_divisional_game("13", "12") is True       # NYR, NYI
        assert nhl_teams.is_divisional_game("13", "1") is False       # NYR, BOS
        assert nhl_teams.is_conference_game("13", "1") is True
        assert nhl_teams.is_conference_game("13", "8") is False       # NYR, LA

    def test_unknown_team_is_none_not_false(self):
        assert nhl_teams.is_divisional_game("13", "999") is None
        assert nhl_teams.is_conference_game("999", "13") is None

    def test_arizona_moved_from_the_pacific_to_the_central(self):
        assert nhl_teams.team_division("24", 2020) == "Western Pacific"
        assert nhl_teams.team_division("24", 2022) == "Western Central"
        assert nhl_teams.is_divisional_game("24", "8", 2020) is True   # ARI, LA
        assert nhl_teams.is_divisional_game("24", "8", 2022) is False

    def test_2021_uses_the_temporary_divisions_and_has_no_conferences(self):
        assert nhl_teams.is_divisional_game("21", "22", 2021) is True  # TOR, VAN -- both North
        assert nhl_teams.is_divisional_game("21", "1", 2021) is False  # TOR, BOS
        assert nhl_teams.is_conference_game("21", "22", 2021) is None
        assert nhl_teams.team_division("124292", 2021) is None


class TestTravelAndTimezone:
    def test_home_team_travels_zero_and_away_team_the_distance_between_arenas(self):
        home_km, away_km = nhl_teams.travel_distances_km("13", "8", "Los Angeles")  # NYR at LA

        assert home_km == 0
        assert 3900 < away_km < 4000

    def test_global_series_venue_gives_both_teams_a_distance(self):
        home_km, away_km = nhl_teams.travel_distances_km("21", "5", "Stockholm")

        assert nhl_teams.is_international_game("Stockholm")
        assert home_km > 6000
        assert away_km > 6000

    def test_unknown_team_has_no_distance(self):
        assert nhl_teams.travel_distances_km("999", "8", None) == (None, None)

    def test_timezone_shift_is_signed_toward_the_east(self):
        assert nhl_teams.timezone_shift_hours("22", "1") == 3    # VAN -> BOS
        assert nhl_teams.timezone_shift_hours("1", "22") == -3
        assert nhl_teams.timezone_shift_hours("1", "13") == 0
        assert nhl_teams.timezone_shift_hours("1", "999") is None
