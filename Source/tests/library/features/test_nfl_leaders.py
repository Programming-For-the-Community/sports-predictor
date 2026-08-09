"""
Unit tests for library.features.nfl's leader-identification pure
functions (identify_starting_qb/identify_lead_rusher/
identify_lead_receiver/identify_top_receivers/identify_top_rushers) --
the volume-ranking half of feature assembly, used both for training-time
label derivation and (via live_features.py) live candidate ranking. No
AWS involved -- every function here takes already-fetched rows and
returns numbers. Split out of what used to be one large test_nfl.py --
see test_nfl_event_features_core.py's own history note for this file's
siblings.
"""
from library.features.nfl import (
    identify_lead_receiver,
    identify_lead_rusher,
    identify_starting_qb,
    identify_top_receivers,
    identify_top_rushers,
)


class TestIdentifyStartingQb:
    def test_picks_most_passing_attempts(self):
        team_games = [
            {"entity_id": "backup-qb", "stat_line": {"passing_attempts": 5, "passing_yards": 40}},
            {"entity_id": "starter-qb", "stat_line": {"passing_attempts": 30, "passing_yards": 280}},
        ]

        starter = identify_starting_qb(team_games)

        assert starter["entity_id"] == "starter-qb"

    def test_returns_full_row_not_just_id(self):
        team_games = [{"entity_id": "starter-qb", "stat_line": {"passing_attempts": 30, "passing_yards": 280}}]

        starter = identify_starting_qb(team_games)

        assert starter == team_games[0]

    def test_ignores_non_passers(self):
        team_games = [
            {"entity_id": "kicker", "stat_line": {"field_goals_made": 2}},
            {"entity_id": "rb", "stat_line": {"rushing_yards": 80}},
        ]

        assert identify_starting_qb(team_games) is None

    def test_empty_team_games_returns_none(self):
        assert identify_starting_qb([]) is None


class TestIdentifyLeadRusher:
    def test_picks_most_rushing_attempts(self):
        team_games = [
            {"entity_id": "backup-rb", "stat_line": {"rushing_attempts": 3, "rushing_yards": 10}},
            {"entity_id": "lead-rb", "stat_line": {"rushing_attempts": 22, "rushing_yards": 95}},
        ]

        leader = identify_lead_rusher(team_games)

        assert leader["entity_id"] == "lead-rb"

    def test_ignores_players_without_rushing_attempts(self):
        team_games = [{"entity_id": "kicker", "stat_line": {"field_goals_made": 2}}]

        assert identify_lead_rusher(team_games) is None

    def test_empty_team_games_returns_none(self):
        assert identify_lead_rusher([]) is None


class TestIdentifyLeadReceiver:
    def test_picks_most_receiving_targets(self):
        team_games = [
            {"entity_id": "wr2", "stat_line": {"receiving_targets": 3, "receiving_yards": 20}},
            {"entity_id": "wr1", "stat_line": {"receiving_targets": 11, "receiving_yards": 130}},
        ]

        leader = identify_lead_receiver(team_games)

        assert leader["entity_id"] == "wr1"

    def test_picks_by_targets_not_receptions(self):
        # A player targeted often but with a low catch rate is still the
        # #1 read -- targets reflect who the offense looked for, not who
        # caught the most.
        team_games = [
            {"entity_id": "sure-hands", "stat_line": {"receiving_targets": 4, "receiving_receptions": 4}},
            {"entity_id": "top-target", "stat_line": {"receiving_targets": 10, "receiving_receptions": 5}},
        ]

        leader = identify_lead_receiver(team_games)

        assert leader["entity_id"] == "top-target"

    def test_ignores_players_without_receiving_targets(self):
        team_games = [{"entity_id": "rb", "stat_line": {"rushing_attempts": 15}}]

        assert identify_lead_receiver(team_games) is None

    def test_empty_team_games_returns_none(self):
        assert identify_lead_receiver([]) is None


class TestIdentifyTopReceivers:
    def test_returns_top_n_sorted_descending_by_targets(self):
        team_games = [
            {"entity_id": "wr3", "stat_line": {"receiving_targets": 5}},
            {"entity_id": "wr1", "stat_line": {"receiving_targets": 11}},
            {"entity_id": "wr2", "stat_line": {"receiving_targets": 8}},
            {"entity_id": "wr4", "stat_line": {"receiving_targets": 2}},
        ]

        top = identify_top_receivers(team_games, n=3)

        assert [row["entity_id"] for row in top] == ["wr1", "wr2", "wr3"]

    def test_fewer_candidates_than_n_returns_all_of_them(self):
        team_games = [{"entity_id": "wr1", "stat_line": {"receiving_targets": 4}}]

        assert len(identify_top_receivers(team_games, n=3)) == 1

    def test_ignores_players_without_receiving_targets(self):
        team_games = [{"entity_id": "rb", "stat_line": {"rushing_attempts": 15}}]

        assert identify_top_receivers(team_games, n=3) == []


class TestIdentifyTopRushers:
    def test_returns_top_n_sorted_descending_by_attempts(self):
        team_games = [
            {"entity_id": "rb2", "stat_line": {"rushing_attempts": 6}},
            {"entity_id": "rb1", "stat_line": {"rushing_attempts": 18}},
        ]

        top = identify_top_rushers(team_games, n=2)

        assert [row["entity_id"] for row in top] == ["rb1", "rb2"]
