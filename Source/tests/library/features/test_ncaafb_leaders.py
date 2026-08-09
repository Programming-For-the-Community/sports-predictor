"""
Unit tests for library.features.ncaafb's leader-identification functions --
identify_starting_qb/identify_lead_rusher/identify_lead_receiver, using
CFBD's own stat_line field names (post normalize.ncaafb's C/ATT split and
CAR/REC renames -- see that module's own docstring for why these differ
from NFL's).
"""
from library.features.ncaafb import identify_lead_receiver, identify_lead_rusher, identify_starting_qb


class TestIdentifyStartingQb:
    def test_picks_most_passing_attempts(self):
        team_games = [
            {"entity_id": "backup-qb", "stat_line": {"passing_attempts": 5, "passing_yards": 40}},
            {"entity_id": "starter-qb", "stat_line": {"passing_attempts": 30, "passing_yards": 280}},
        ]

        starter = identify_starting_qb(team_games)

        assert starter["entity_id"] == "starter-qb"

    def test_no_passing_attempts_in_the_game_yields_none(self):
        team_games = [{"entity_id": "k1", "stat_line": {"kicking_field_goals_made": 2}}]

        assert identify_starting_qb(team_games) is None


class TestIdentifyLeadRusher:
    def test_picks_most_rushing_attempts(self):
        team_games = [
            {"entity_id": "rb2", "stat_line": {"rushing_attempts": 4, "rushing_yards": 12}},
            {"entity_id": "rb1", "stat_line": {"rushing_attempts": 22, "rushing_yards": 110}},
        ]

        leader = identify_lead_rusher(team_games)

        assert leader["entity_id"] == "rb1"


class TestIdentifyLeadReceiver:
    def test_picks_most_receptions_not_targets(self):
        # CFBD has no targets stat at all -- receptions is the volume
        # signal here, unlike NFL's identify_lead_receiver.
        team_games = [
            {"entity_id": "wr2", "stat_line": {"receiving_receptions": 2, "receiving_yards": 30}},
            {"entity_id": "wr1", "stat_line": {"receiving_receptions": 8, "receiving_yards": 95}},
        ]

        leader = identify_lead_receiver(team_games)

        assert leader["entity_id"] == "wr1"
