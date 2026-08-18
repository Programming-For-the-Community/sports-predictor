"""
Unit tests for library.features.nba_cup_groups.cup_group_for_team.
"""
from library.features.nba_cup_groups import CUP_GROUPS, cup_group_for_team


class TestCupGroupForTeam:
    def test_finds_a_known_teams_group(self):
        assert cup_group_for_team(2026, "2") == "Eastern B"  # BOS

    def test_finds_a_western_conference_team(self):
        assert cup_group_for_team(2026, "13") == "Western B"  # LAL

    def test_finds_a_2027_team(self):
        assert cup_group_for_team(2027, "18") == "Eastern B"  # NY

    def test_none_when_season_is_not_in_the_table_yet(self):
        assert cup_group_for_team(2028, "2") is None

    def test_none_when_season_is_none(self):
        assert cup_group_for_team(None, "2") is None

    def test_none_when_team_id_is_not_in_any_group(self):
        assert cup_group_for_team(2026, "999") is None

    def test_every_group_has_exactly_five_teams(self):
        for season in CUP_GROUPS:
            for conference in CUP_GROUPS[season].values():
                for team_ids in conference.values():
                    assert len(team_ids) == 5

    def test_all_thirty_teams_are_assigned_with_no_duplicates(self):
        for season in CUP_GROUPS:
            all_ids = [
                team_id
                for conference in CUP_GROUPS[season].values()
                for team_ids in conference.values()
                for team_id in team_ids
            ]
            assert len(all_ids) == 30
            assert len(set(all_ids)) == 30
