"""
Unit tests for library.features.common's ranking and injury-severity
primitives: rank_by_average_stat (ranks by average, not one bursty
single-game total), _injury_status_ordinal, and _team_injury_count. No
AWS involved. Split out of what used to be one large test_common.py --
see test_common_elo.py's own history note.
"""
import pytest

from library.features.common import _injury_status_ordinal, _team_injury_count, rank_by_average_stat


class TestRankByAverageStat:
    def test_ranks_by_average_not_a_single_huge_game(self):
        # "player-b" had one huge game but a lower average overall --
        # this is exactly the case single-game volume (_identify_leader's
        # approach) would get wrong for a bursty stat like sacks.
        histories = {
            "player-a": [
                {"stat_line": {"defensive_sacks": 2.0}},
                {"stat_line": {"defensive_sacks": 2.0}},
                {"stat_line": {"defensive_sacks": 2.0}},
            ],
            "player-b": [
                {"stat_line": {"defensive_sacks": 5.0}},
                {"stat_line": {"defensive_sacks": 0.0}},
                {"stat_line": {"defensive_sacks": 0.0}},
            ],
        }

        ranked = rank_by_average_stat(histories, "defensive_sacks", n=2)

        assert ranked == ["player-a", "player-b"]

    def test_candidates_with_no_recorded_value_are_excluded(self):
        histories = {"offense-player": [{"stat_line": {"passing_yards": 250}}]}

        assert rank_by_average_stat(histories, "defensive_sacks", n=3) == []

    def test_respects_n(self):
        histories = {
            "a": [{"stat_line": {"defensive_sacks": 3.0}}],
            "b": [{"stat_line": {"defensive_sacks": 2.0}}],
            "c": [{"stat_line": {"defensive_sacks": 1.0}}],
        }

        assert rank_by_average_stat(histories, "defensive_sacks", n=1) == ["a"]


class TestInjuryStatusOrdinal:
    def test_none_when_injuries_is_none(self):
        assert _injury_status_ordinal(None, "mahomes") is None

    def test_none_when_entity_id_is_none(self):
        assert _injury_status_ordinal([{"entity_id": "mahomes", "status": "Out"}], None) is None

    def test_zero_when_player_not_on_the_report(self):
        assert _injury_status_ordinal([{"entity_id": "someone-else", "status": "Out"}], "mahomes") == 0

    def test_zero_when_report_is_empty_list(self):
        assert _injury_status_ordinal([], "mahomes") == 0

    @pytest.mark.parametrize("status,expected", [("Questionable", 1), ("Doubtful", 2), ("Out", 3)])
    def test_maps_known_statuses_to_severity_order(self, status, expected):
        assert _injury_status_ordinal([{"entity_id": "mahomes", "status": status}], "mahomes") == expected

    def test_unrecognized_status_falls_back_to_1(self):
        assert _injury_status_ordinal([{"entity_id": "mahomes", "status": "Injured Reserve"}], "mahomes") == 1


class TestTeamInjuryCount:
    def test_none_when_injuries_is_none(self):
        assert _team_injury_count(None) is None

    def test_zero_for_empty_report(self):
        assert _team_injury_count([]) == 0

    def test_counts_only_doubtful_and_out(self):
        injuries = [
            {"entity_id": "1", "status": "Out"},
            {"entity_id": "2", "status": "Doubtful"},
            {"entity_id": "3", "status": "Questionable"},
            {"entity_id": "4", "status": "Out"},
        ]
        assert _team_injury_count(injuries) == 3
