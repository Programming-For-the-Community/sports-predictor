"""
Unit tests for library.features.nba_teams -- static division/coordinate
tables and their thin geo.py wrappers. No AWS involved.
"""
from library.features import nba_teams


class TestStaticTables:
    def test_every_team_id_is_present_in_both_tables_no_gaps(self):
        # Cross-consistency, not correctness of any individual value --
        # a team present in one table but not the other would silently
        # resolve to None for whichever feature reads the missing one.
        assert set(nba_teams.TEAM_DIVISIONS) == set(nba_teams.TEAM_COORDINATES)

    def test_exactly_thirty_franchises(self):
        assert len(nba_teams.TEAM_DIVISIONS) == 30
        assert len(nba_teams.TEAM_COORDINATES) == 30

    def test_six_divisions_five_teams_each(self):
        from collections import Counter
        counts = Counter(nba_teams.TEAM_DIVISIONS.values())
        assert len(counts) == 6
        assert set(counts.values()) == {5}


class TestIsRealFranchiseMatchup:
    def test_true_when_both_participants_are_known_franchises(self):
        event = {"participants": [{"entity_id": "2"}, {"entity_id": "17"}]}
        assert nba_teams.is_real_franchise_matchup(event) is True

    def test_false_when_a_participant_is_not_a_known_franchise(self):
        # e.g. an NBA All-Star Game roster id -- see this function's own
        # docstring.
        event = {"participants": [{"entity_id": "2"}, {"entity_id": "9001"}]}
        assert nba_teams.is_real_franchise_matchup(event) is False

    def test_true_for_empty_participants(self):
        assert nba_teams.is_real_franchise_matchup({"participants": []}) is True


class TestIsDivisionalGame:
    def test_true_for_same_division_teams(self):
        # Both Atlantic (BOS=2, NY=18).
        assert nba_teams.is_divisional_game("2", "18") is True

    def test_false_for_different_division_teams(self):
        # BOS (Atlantic) vs LAL (Pacific).
        assert nba_teams.is_divisional_game("2", "13") is False

    def test_none_for_unknown_team(self):
        assert nba_teams.is_divisional_game("2", "9001") is None


class TestIsInternationalGame:
    def test_true_for_known_international_venue(self):
        assert nba_teams.is_international_game("Mexico City") is True

    def test_false_for_domestic_venue_or_none(self):
        assert nba_teams.is_international_game("Boston") is False
        assert nba_teams.is_international_game(None) is False


class TestTravelDistancesKm:
    def test_zero_for_same_market_teams(self):
        # Not a real matchup (a team never plays itself), but proves the
        # underlying geo.py haversine call resolves to 0 for identical
        # coordinates rather than blowing up.
        home_travel, away_travel = nba_teams.travel_distances_km("2", "2", None)
        assert home_travel == 0
        assert away_travel == 0

    def test_home_team_travels_zero_away_team_travels_a_real_distance(self):
        # BOS hosting LAL -- home team is always "at home" (0 km); away
        # team's travel is a real, positive cross-country distance.
        home_travel, away_travel = nba_teams.travel_distances_km(away_id="13", home_id="2", venue_city=None)
        assert home_travel == 0
        assert away_travel > 3000

    def test_none_for_unknown_team(self):
        home_travel, away_travel = nba_teams.travel_distances_km("9001", "2", None)
        assert home_travel is None
        assert away_travel is None
