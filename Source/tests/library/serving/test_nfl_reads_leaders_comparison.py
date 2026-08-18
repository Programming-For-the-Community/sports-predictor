"""
Unit tests for library.serving.nfl_reads._leaders_comparison -- the
player-prop predicted-vs-actual read for a completed event: parsing
player-prop model_key rows out of the predictions-table audit trail,
bucketing each candidate by current team_id, and the defensive top-3-by-
predicted-yards re-cap (guards against an event whose audit trail holds
more than 3 recorded receiving predictions). Storage/predictions-table
objects are mocked. Split out of what used to be one large
test_nfl_reads.py -- see test_nfl_reads_list_events.py's own history note.
"""
from unittest.mock import MagicMock

from library.serving import nfl_reads

from _nfl_reads_test_helpers import completed_event as _completed_event
from _nfl_reads_test_helpers import prediction_row as _prediction_row


class TestLeadersComparison:
    def test_none_when_nothing_was_ever_predicted(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        event = _completed_event("EVT#1", 2025, "12", "13", 24, 17)

        assert nfl_reads._leaders_comparison(storage, predictions_table.query.return_value, "nfl", event) is None
        # No predicted rows at all -- shouldn't even bother looking up
        # actual stats.
        storage.get_player_game_stats_for_event.assert_not_called()

    def test_none_for_a_malformed_event_with_no_home_away_roles(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        event = {"event_key": "EVT#1", "participants": [{"entity_id": "12", "role": "unknown"}]}

        assert nfl_reads._leaders_comparison(storage, predictions_table.query.return_value, "nfl", event) is None

    def test_ignores_non_player_prop_model_keys_in_the_same_query_results(self):
        storage = MagicMock()
        storage.get_player_game_stats_for_event.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            _prediction_row("MODEL#win-probability#v6", {"home_win_probability": 0.71}),
            _prediction_row("MODEL#score-margin#v3", {"value": 6.2}),
        ]
        event = _completed_event("EVT#1", 2025, "12", "13", 24, 17)

        assert nfl_reads._leaders_comparison(storage, predictions_table.query.return_value, "nfl", event) is None

    def test_receiving_leaders_are_grouped_as_a_list(self):
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, entity_id, entity_type: {
            "wr1": {"entity_id": "wr1", "metadata": {"team_id": "12"}, "name": "WR One"},
            "wr2": {"entity_id": "wr2", "metadata": {"team_id": "12"}, "name": "WR Two"},
        }[entity_id]
        storage.get_player_game_stats_for_event.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            _prediction_row("MODEL#player-prop-receiving-yards#v1#PLAYER#wr1", {"value": 80.0}),
            _prediction_row("MODEL#player-prop-receiving-yards#v1#PLAYER#wr2", {"value": 60.0}),
        ]
        event = _completed_event("EVT#1", 2025, "12", "13", 24, 17)

        result = nfl_reads._leaders_comparison(storage, predictions_table.query.return_value, "nfl", event)

        assert {r["entity_id"] for r in result["home"]["receiving"]} == {"wr1", "wr2"}
        assert result["away"]["receiving"] == []

    def test_receiving_is_capped_at_top_3_by_predicted_yards(self):
        # Guards against an event whose audit trail holds more than 3
        # recorded receiving predictions (e.g. one predicted before
        # event_prediction.py started capping what it writes) -- the
        # comparison should still only ever show the top 3, same as the
        # pre-game leaders block did.
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, entity_id, entity_type: {
            "entity_id": entity_id, "metadata": {"team_id": "12"}, "name": entity_id,
        }
        storage.get_player_game_stats_for_event.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            _prediction_row("MODEL#player-prop-receiving-yards#v1#PLAYER#wr1", {"value": 40.0}),
            _prediction_row("MODEL#player-prop-receiving-yards#v1#PLAYER#wr2", {"value": 90.0}),
            _prediction_row("MODEL#player-prop-receiving-yards#v1#PLAYER#wr3", {"value": 60.0}),
            _prediction_row("MODEL#player-prop-receiving-yards#v1#PLAYER#wr4", {"value": 20.0}),
            _prediction_row("MODEL#player-prop-receiving-yards#v1#PLAYER#wr5", {"value": 75.0}),
        ]
        event = _completed_event("EVT#1", 2025, "12", "13", 24, 17)

        result = nfl_reads._leaders_comparison(storage, predictions_table.query.return_value, "nfl", event)

        assert [r["entity_id"] for r in result["home"]["receiving"]] == ["wr2", "wr5", "wr3"]

    def test_predicted_with_no_matching_actual_stat_line_still_shows_predicted(self):
        # Predicted as a leader candidate, but didn't record a stat line
        # for this game (DNP, benched, etc.).
        storage = MagicMock()
        storage.get_entity.return_value = {"entity_id": "qb1", "metadata": {"team_id": "12"}}
        storage.get_player_game_stats_for_event.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            _prediction_row("MODEL#player-prop-passing-yards#v3#PLAYER#qb1", {"value": 267.0}),
        ]
        event = _completed_event("EVT#1", 2025, "12", "13", 24, 17)

        result = nfl_reads._leaders_comparison(storage, predictions_table.query.return_value, "nfl", event)

        assert result["home"]["passing"]["predicted"] == {"passing_yards": 267.0}
        assert result["home"]["passing"]["actual"] == {}

    def test_skips_a_candidate_whose_team_matches_neither_home_nor_away(self):
        # A recorded prediction exists (so this isn't the "nothing was
        # ever predicted" None case), but the one candidate's team_id
        # (e.g. traded since the prediction was recorded) matches neither
        # side -- skipped rather than guessed into a bucket.
        storage = MagicMock()
        storage.get_entity.return_value = {"entity_id": "qb1", "metadata": {"team_id": "99"}}
        storage.get_player_game_stats_for_event.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            _prediction_row("MODEL#player-prop-passing-yards#v3#PLAYER#qb1", {"value": 267.0}),
        ]
        event = _completed_event("EVT#1", 2025, "12", "13", 24, 17)

        result = nfl_reads._leaders_comparison(storage, predictions_table.query.return_value, "nfl", event)

        assert result["home"]["passing"] is None
        assert result["away"]["passing"] is None

    def test_away_team_candidate_lands_in_the_away_bucket(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"entity_id": "rb1", "metadata": {"team_id": "13"}}
        storage.get_player_game_stats_for_event.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            _prediction_row("MODEL#player-prop-rushing-yards#v2#PLAYER#rb1", {"value": 95.0}),
        ]
        event = _completed_event("EVT#1", 2025, "12", "13", 24, 17)

        result = nfl_reads._leaders_comparison(storage, predictions_table.query.return_value, "nfl", event)

        assert result["home"]["rushing"] == []
        assert [r["entity_id"] for r in result["away"]["rushing"]] == ["rb1"]
