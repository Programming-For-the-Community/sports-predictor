"""
Unit tests for library.serving.nhl_reads -- the hockey fields on an event
list entry and the hockey leader categories. The shared day-grouped list
itself is covered by test_nba_reads.py / test_common.py.
"""
from unittest.mock import MagicMock, patch

from library.serving import nhl_reads


def _event(status="scheduled", **fields):
    return {
        "event_key": "SPORT#NHL#EVENT#9", "event_id": "9", "event_date": "2026-10-10", "status": status,
        "kickoff_time": "2026-10-10T23:00Z", "season": 2027,
        "participants": [
            {"entity_id": "13", "role": "home", "result": {"score": 4, "won": True, "period_scores": [1, 1, 1, 0, 1], "regulation_score": 3}},
            {"entity_id": "1", "role": "away", "result": {"score": 3, "won": False, "period_scores": [1, 1, 1, 0, 0], "regulation_score": 3}},
        ],
        **fields,
    }


def _prop_row(stat, entity_id, value, generated_at="2026-10-10T23:20:00+00:00"):
    return {
        "model_key": f"MODEL#player-prop-{stat.replace('_', '-')}#v1#PLAYER#{entity_id}",
        "predicted_value": {"value": value}, "generated_at": generated_at,
    }


class TestCategories:
    def test_every_prop_stat_belongs_to_exactly_one_category(self):
        assert nhl_reads._STAT_CATEGORY == {
            "goals": "scoring", "assists": "scoring", "shots_total": "shooting", "hits": "physical", "saves": "goaltending",
        }
        assert nhl_reads.CATEGORY_PRIMARY_STAT == {
            "scoring": "goals", "shooting": "shots_total", "physical": "hits", "goaltending": "saves",
        }
        assert nhl_reads.LEADER_CATEGORY_LIMITS["goaltending"] == 1


class TestProbableGoalies:
    def test_named_with_espns_status_and_a_side_with_none_is_omitted(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Igor Shesterkin"}
        event = _event(home_probable_goalie_id="3151297", home_probable_goalie_status="Expected")

        goalies = nhl_reads.probable_goalies(storage, "nhl", event)

        assert goalies == {"home": {"entity_id": "3151297", "name": "Igor Shesterkin", "status": "Expected"}}
        storage.get_entity.assert_called_once_with("nhl", "3151297", "player")

    def test_unknown_goalie_entity_still_lists_the_id(self):
        storage = MagicMock()
        storage.get_entity.return_value = None

        goalies = nhl_reads.probable_goalies(storage, "nhl", _event(away_probable_goalie_id="7", away_probable_goalie_status="Confirmed"))

        assert goalies == {"away": {"entity_id": "7", "name": None, "status": "Confirmed"}}


class TestLeadersComparison:
    def test_groups_predicted_and_actual_by_hockey_category(self):
        storage = MagicMock()
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "s1", "stat_line": {"points": 2, "goals": 1, "assists": 1, "shots_total": 5, "hits": 1}},
            {"entity_id": "g1", "stat_line": {"saves": 31, "goals_against": 3}},
        ]
        storage.get_entity.side_effect = lambda sport, entity_id, kind: {
            "name": entity_id.upper(), "metadata": {"team_id": "13" if entity_id != "g1" else "1"},
        }
        rows = [
            _prop_row("points", "s1", 1.1), _prop_row("goals", "s1", 0.4), _prop_row("shots_total", "s1", 3.6),
            _prop_row("hits", "s1", 1.8), _prop_row("saves", "g1", 27.5), _prop_row("goals_against", "g1", 2.9),
        ]

        comparison = nhl_reads._leaders_comparison(storage, rows, "nhl", _event(status="completed"))

        assert set(comparison["home"]) == {"scoring", "shooting", "physical", "goaltending"}
        [scorer] = comparison["home"]["scoring"]
        # A stat the app does not show (points, goals against) is left out.
        assert scorer["predicted"] == {"goals": 0.4}
        assert scorer["actual"] == {"goals": 1}
        assert comparison["home"]["shooting"][0]["predicted"] == {"shots_total": 3.6}
        assert comparison["home"]["goaltending"] == []
        [goalie] = comparison["away"]["goaltending"]
        assert (goalie["predicted"]["saves"], goalie["actual"]["saves"]) == (27.5, 31)

    def test_none_without_any_prop_predictions(self):
        assert nhl_reads._leaders_comparison(MagicMock(), [], "nhl", _event(status="completed")) is None


class TestListEvents:
    def test_entries_carry_the_overtime_flags_goalies_and_period_scores(self):
        storage = MagicMock()
        event = _event(
            went_to_overtime=True, decided_by_shootout=True,
            home_probable_goalie_id="g13", home_probable_goalie_status="Confirmed",
        )
        storage.get_all_events.return_value = [event]
        storage.get_entity.return_value = {"name": "Named", "metadata": {"abbreviation": "NYR"}}

        with patch.object(nhl_reads.common, "_next_day_events", side_effect=lambda events: events), \
             patch.object(nhl_reads.common, "prefetch_participant_teams", return_value={}):
            result = nhl_reads.list_events(storage, MagicMock(), "nhl", "scheduled")

        [entry] = result["events"]
        assert (entry["went_to_overtime"], entry["decided_by_shootout"]) == (True, True)
        assert entry["goalies"] == {"home": {"entity_id": "g13", "name": "Named", "status": "Confirmed"}}
        assert entry["participants"][0]["result"]["period_scores"] == [1, 1, 1, 0, 1]
        assert entry["participants"][0]["abbreviation"] == "NYR"
