"""
Unit tests for library.serving.nfl_reads.list_events -- GET /nfl/events'
week-scoping (soonest upcoming / most recent completed), venue passthrough,
Pro Bowl exclusion, round labeling, and per-event prediction_comparison/
leaders_comparison attachment for completed events. Storage/predictions-
table objects are mocked. Split out of what used to be one large
test_nfl_reads.py -- see test_nfl_reads_round_label.py,
test_nfl_reads_leaders_comparison.py, test_nfl_reads_models.py, and
test_nfl_reads_season_projection.py for this file's siblings, one per
concern. Shared fixtures live in _nfl_reads_test_helpers.py (not
test_-prefixed, so pytest never collects it as a test module itself).
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

from library.serving import nfl_reads

from _nfl_reads_test_helpers import completed_event as _completed_event
from _nfl_reads_test_helpers import prediction_row as _prediction_row
from _nfl_reads_test_helpers import scheduled_event as _scheduled_event

# "scheduled" event_dates are relative to today, not hardcoded -- list_events'
# soonest-upcoming-week scoping ignores anything more than a few days in the
# past (see nfl_reads._STALE_SCHEDULED_GRACE_DAYS), so a fixed past date
# would silently drop out of the result as real time moves forward.
def _future(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


class TestListEvents:
    def test_returns_events_for_the_requested_status(self):
        storage = MagicMock()
        event_date = _future(4)
        storage.get_all_events.return_value = [
            {
                "event_id": "401547417", "event_date": event_date, "kickoff_time": f"{event_date}T20:25Z",
                "status": "scheduled", "season": 2025, "season_type": 2, "week": 4,
                "participants": [{"entity_id": "12", "role": "home"}, {"entity_id": "24", "role": "away"}],
            },
        ]

        result = nfl_reads.list_events(storage, MagicMock(), "nfl", "scheduled")

        assert result["sport"] == "nfl"
        assert result["events"][0]["event_id"] == "401547417"
        assert result["events"][0]["kickoff_time"] == f"{event_date}T20:25Z"
        storage.get_all_events.assert_called_once_with("nfl", status="scheduled")

    def test_venue_fields_pass_through_when_present(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [
            {
                **_scheduled_event("EVT#1", 2025, _future(4), "12", "24"),
                "venue_name": "Arrowhead Stadium", "venue_city": "Kansas City", "venue_state": "MO",
            },
        ]

        result = nfl_reads.list_events(storage, MagicMock(), "nfl", "scheduled")

        entry = result["events"][0]
        assert entry["venue_name"] == "Arrowhead Stadium"
        assert entry["venue_city"] == "Kansas City"
        assert entry["venue_state"] == "MO"

    def test_venue_fields_are_none_when_absent(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_scheduled_event("EVT#1", 2025, _future(4), "12", "24")]

        result = nfl_reads.list_events(storage, MagicMock(), "nfl", "scheduled")

        entry = result["events"][0]
        assert entry["venue_name"] is None
        assert entry["venue_city"] is None
        assert entry["venue_state"] is None

    def test_completed_status_scopes_to_the_most_recent_week_only(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [
            _completed_event("EVT#1", 2025, "12", "13", 24, 17, event_date="2025-09-07", week=1),
            _completed_event("EVT#2", 2025, "12", "13", 20, 10, event_date="2025-09-14", week=2),
            _completed_event("EVT#3", 2025, "12", "13", 30, 27, event_date="2025-09-15", week=2),
        ]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        assert [e["event_id"] for e in result["events"]] == ["EVT#2", "EVT#3"]

    def test_scheduled_status_scopes_to_the_soonest_week_only(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _scheduled_event("EVT#1", 2025, _future(11), "12", "13", week=3),
            _scheduled_event("EVT#2", 2025, _future(4), "12", "13", week=2),
            _scheduled_event("EVT#3", 2025, _future(4), "1", "2", week=2),
        ]

        result = nfl_reads.list_events(storage, MagicMock(), "nfl", "scheduled")

        assert [e["event_id"] for e in result["events"]] == ["EVT#2", "EVT#3"]

    def test_scheduled_status_is_empty_when_the_next_week_has_not_been_ingested_yet(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []

        result = nfl_reads.list_events(storage, MagicMock(), "nfl", "scheduled")

        assert result["events"] == []

    def test_a_stale_never_played_scheduled_event_does_not_mask_a_real_upcoming_week(self):
        storage = MagicMock()
        stale_date = (date.today() - timedelta(days=400)).isoformat()
        storage.get_all_events.return_value = [
            _scheduled_event("STALE", 2024, stale_date, "12", "13", week=1),
            _scheduled_event("EVT#1", 2025, _future(4), "12", "13", week=2),
            _scheduled_event("EVT#2", 2025, _future(4), "1", "2", week=2),
        ]

        result = nfl_reads.list_events(storage, MagicMock(), "nfl", "scheduled")

        assert [e["event_id"] for e in result["events"]] == ["EVT#1", "EVT#2"]

    def test_completed_events_include_prediction_comparison_when_one_was_logged(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            _prediction_row("MODEL#win-probability#v6", {"home_win_probability": 0.71, "model_version": 6}),
            _prediction_row("MODEL#score-margin#v3", {"value": 6.2, "model_version": 3}),
            _prediction_row("MODEL#home-score#v2", {"value": 27.4, "model_version": 2}),
            _prediction_row("MODEL#away-score#v2", {"value": 21.2, "model_version": 2}),
        ]
        storage.get_all_events.return_value = [_completed_event("EVT#1", 2025, "12", "13", 24, 17)]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        comparison = result["events"][0]["prediction_comparison"]
        assert comparison["predicted_home_win_probability"] == 0.71
        assert comparison["predicted_home_won"] is True
        assert comparison["actual_home_won"] is True
        assert comparison["correct"] is True
        assert comparison["actual_margin"] == 7
        assert comparison["actual_home_score"] == 24
        assert comparison["actual_away_score"] == 17

    def test_excludes_the_pro_bowl_from_the_list(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _completed_event("EVT#REAL", 2025, "12", "13", 24, 17, event_date="2025-09-14", week=2),
            # AFC (31) vs NFC (32) -- the Pro Bowl, same week as the real game.
            _completed_event("EVT#PROBOWL", 2025, "31", "32", 40, 35, event_date="2025-09-14", week=2),
        ]
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        assert [e["event_id"] for e in result["events"]] == ["EVT#REAL"]

    def test_postseason_events_carry_a_round_label(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [
            _completed_event("EVT#WC", 2025, "12", "13", 24, 17, event_date="2026-01-11", season_type=3, week=1),
        ]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        assert result["events"][0]["round"] == "Wild Card"

    def test_regular_season_events_have_no_round_label(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [
            _completed_event("EVT#1", 2025, "12", "13", 24, 17, season_type=2, week=4),
        ]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        assert result["events"][0]["round"] is None

    def test_completed_events_have_no_comparison_when_nothing_was_ever_predicted(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [_completed_event("EVT#1", 2025, "12", "13", 24, 17)]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        assert result["events"][0]["prediction_comparison"] is None

    def test_completed_events_include_leaders_comparison_when_one_was_recorded(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_completed_event("EVT#1", 2025, "12", "13", 24, 17)]
        storage.get_entity.return_value = {"entity_id": "qb1", "metadata": {"team_id": "12"}, "name": "Patrick Mahomes"}
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "qb1", "team_id": "12", "stat_line": {"passing_yards": 289}},
        ]
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            _prediction_row("MODEL#player-prop-passing-yards#v3#PLAYER#qb1", {"value": 267.0}),
        ]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        passing = result["events"][0]["leaders_comparison"]["home"]["passing"]
        assert passing["entity_id"] == "qb1"
        assert passing["predicted"] == {"passing_yards": 267.0}
        assert passing["actual"] == {"passing_yards": 289}
