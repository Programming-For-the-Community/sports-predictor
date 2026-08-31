"""
Unit tests for aws-lambdas/f1/predict/live_features.py -- the highest-risk
new logic in the F1 serving Lambda pair (re-deriving each driver's/
constructor's own rolling history from CURRENT DynamoDB state, plus the
grid-from-qualifying substitution live_features.py's own docstring
explains). library/features/f1.py's own pure builders are exercised for
real (they're already covered elsewhere), not mocked -- only storage is.
"""
from unittest.mock import MagicMock

import pytest

import live_features
from library.schema.keys import event_key as build_event_key


def _result(finish_position=None, grid_position=None, status=None, points=0.0, qualifying=None):
    return {
        "finish_position": finish_position, "grid_position": grid_position, "status": status,
        "points": points, "fastest_lap": False, "laps_completed": None, "qualifying": qualifying,
    }


def _participant(entity_id, constructor_entity_id="red_bull", **result_kwargs):
    return {"entity_id": entity_id, "constructor_entity_id": constructor_entity_id, "result": _result(**result_kwargs)}


def _field_event(event_id, event_date, participants, circuit_id="bahrain", status="scheduled"):
    return {
        "event_key": build_event_key("f1", event_id), "event_id": event_id, "event_type": "field",
        "event_date": event_date, "circuit_id": circuit_id, "status": status, "season": 2024, "week": 1,
        "participants": participants,
    }


def _sprint_event(event_id, event_date, participants, circuit_id="shanghai", status="scheduled"):
    return {
        "event_key": build_event_key("f1", event_id), "event_id": event_id, "event_type": "sprint",
        "event_date": event_date, "circuit_id": circuit_id, "status": status, "season": 2024, "week": 5,
        "participants": participants,
    }


class TestBuildLiveFieldFeaturesErrors:
    def test_raises_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_field_features(storage, "f1", "missing")

    def test_raises_malformed_for_wrong_event_type(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "sprint"}
        with pytest.raises(live_features.MalformedEventError):
            live_features.build_live_field_features(storage, "f1", "2024-5-sprint")


class TestBuildLiveFieldFeatures:
    def test_returns_a_row_per_participant_and_per_constructor(self):
        event = _field_event("2024-2", "2024-03-09", [_participant("max_verstappen"), _participant("lewis_hamilton", "mercedes")])
        storage = MagicMock()
        storage.get_event.return_value = event
        storage.get_all_events.return_value = [event]

        built = live_features.build_live_field_features(storage, "f1", "2024-2")

        assert set(built["driver_rows"]) == {"max_verstappen", "lewis_hamilton"}
        assert set(built["constructor_rows"]) == {"red_bull", "mercedes"}

    def test_rolling_history_pulls_prior_results_strictly_before_this_race(self):
        prior = _field_event("2024-1", "2024-03-02", [_participant("max_verstappen", finish_position=1, status="finished")])
        current = _field_event("2024-2", "2024-03-09", [_participant("max_verstappen")])
        storage = MagicMock()
        storage.get_event.return_value = current
        storage.get_all_events.return_value = [prior, current]

        built = live_features.build_live_field_features(storage, "f1", "2024-2")

        assert built["driver_rows"]["max_verstappen"]["starts"] == 1
        assert built["driver_rows"]["max_verstappen"]["avg_finish_position"] == 1

    def test_circuit_fit_only_counts_races_at_the_same_circuit(self):
        same_circuit = _field_event("2024-1", "2024-03-02", [_participant("max_verstappen", finish_position=2, status="finished")], circuit_id="bahrain")
        other_circuit = _field_event("2024-3", "2024-03-15", [_participant("max_verstappen", finish_position=5, status="finished")], circuit_id="jeddah")
        current = _field_event("2025-1", "2025-03-01", [_participant("max_verstappen")], circuit_id="bahrain")
        storage = MagicMock()
        storage.get_event.return_value = current
        storage.get_all_events.return_value = [same_circuit, other_circuit, current]

        built = live_features.build_live_field_features(storage, "f1", "2025-1")

        assert built["driver_rows"]["max_verstappen"]["circuit_starts"] == 1
        assert built["driver_rows"]["max_verstappen"]["circuit_avg_finish_position"] == 2

    def test_constructor_history_is_pooled_across_both_drivers(self):
        prior = _field_event("2024-1", "2024-03-02", [
            _participant("max_verstappen", "red_bull", finish_position=1, status="finished"),
            _participant("sergio_perez", "red_bull", finish_position=4, status="finished"),
        ])
        current = _field_event("2024-2", "2024-03-09", [_participant("max_verstappen", "red_bull")])
        storage = MagicMock()
        storage.get_event.return_value = current
        storage.get_all_events.return_value = [prior, current]

        built = live_features.build_live_field_features(storage, "f1", "2024-2")

        # Pooled across both red_bull drivers -- 2 result rows, not 1.
        assert built["driver_rows"]["max_verstappen"]["constructor_starts"] == 2

    def test_grid_position_is_substituted_from_qualifying_when_the_real_grid_is_unknown(self):
        current = _field_event("2024-2", "2024-03-09", [_participant("max_verstappen", qualifying={"position": 3, "gap_to_pole_seconds": 0.2})])
        storage = MagicMock()
        storage.get_event.return_value = current
        storage.get_all_events.return_value = [current]

        built = live_features.build_live_field_features(storage, "f1", "2024-2")

        assert built["driver_rows"]["max_verstappen"]["grid_position"] == 3

    def test_real_grid_position_is_never_overridden_by_qualifying(self):
        current = _field_event("2024-2", "2024-03-09", [
            _participant("max_verstappen", grid_position=1, finish_position=1, status="finished", qualifying={"position": 3}),
        ])
        storage = MagicMock()
        storage.get_event.return_value = current
        storage.get_all_events.return_value = [current]

        built = live_features.build_live_field_features(storage, "f1", "2024-2")

        assert built["driver_rows"]["max_verstappen"]["grid_position"] == 1

    def test_qualifying_history_skips_races_with_no_merged_qualifying(self):
        no_qualifying = _field_event("2024-1", "2024-03-02", [_participant("max_verstappen", finish_position=1, status="finished", qualifying=None)])
        with_qualifying = _field_event("2024-2", "2024-03-09", [_participant("max_verstappen", finish_position=1, status="finished", qualifying={"position": 1, "gap_to_pole_seconds": 0.0})])
        current = _field_event("2024-3", "2024-03-15", [_participant("max_verstappen")])
        storage = MagicMock()
        storage.get_event.return_value = current
        storage.get_all_events.return_value = [no_qualifying, with_qualifying, current]

        built = live_features.build_live_field_features(storage, "f1", "2024-3")

        assert built["driver_rows"]["max_verstappen"]["qualifying_qualifying_sessions"] == 1


class TestBuildLiveFieldFeaturesProjectedFallback:
    """A "scheduled" stub event (library/normalize/f1.py's schedule_
    payload_to_scheduled_events -- always empty participants, since
    Jolpica has no pre-race entry-list endpoint at all) must still score
    against the CURRENT roster instead of coming back with nothing --
    real gap found live 2026-08-31: every future F1 race's own detail
    page showed no data at all until this fallback existed."""

    def test_empty_participants_falls_back_to_the_current_roster(self):
        most_recent_completed = _field_event(
            "2024-1", "2024-03-02",
            [_participant("max_verstappen", "red_bull"), _participant("lewis_hamilton", "mercedes")],
            status="completed",
        )
        future_stub = _field_event("2024-9", "2024-06-01", [], status="scheduled")
        storage = MagicMock()
        storage.get_event.return_value = future_stub
        storage.get_all_events.return_value = [most_recent_completed, future_stub]

        built = live_features.build_live_field_features(storage, "f1", "2024-9")

        assert set(built["driver_rows"]) == {"max_verstappen", "lewis_hamilton"}
        assert set(built["constructor_rows"]) == {"red_bull", "mercedes"}

    def test_a_real_already_underway_field_is_never_overridden_by_the_fallback(self):
        # Real (non-empty) participants -- must use the event's OWN field,
        # not the fallback roster, even though other completed events exist.
        older_roster = _field_event("2024-1", "2024-03-02", [_participant("driver_a")], status="completed")
        current_event = _field_event("2024-2", "2024-03-09", [_participant("driver_b")], status="scheduled")
        storage = MagicMock()
        storage.get_event.return_value = current_event
        storage.get_all_events.return_value = [older_roster, current_event]

        built = live_features.build_live_field_features(storage, "f1", "2024-2")

        assert set(built["driver_rows"]) == {"driver_b"}

    def test_no_completed_field_race_ever_stored_returns_an_empty_field_not_a_crash(self):
        future_stub = _field_event("2024-1", "2024-03-02", [], status="scheduled")
        storage = MagicMock()
        storage.get_event.return_value = future_stub
        storage.get_all_events.return_value = [future_stub]

        built = live_features.build_live_field_features(storage, "f1", "2024-1")

        assert built["driver_rows"] == {}
        assert built["constructor_rows"] == {}


class TestCurrentRoster:
    def test_resolves_from_the_most_recently_completed_field_race(self):
        older = _field_event("2024-1", "2024-03-02", [_participant("driver_a", "red_bull")], status="completed")
        newer = _field_event("2024-2", "2024-03-09", [_participant("driver_b", "mercedes")], status="completed")
        storage = MagicMock()

        driver_ids, driver_to_constructor = live_features.current_roster(storage, "f1", all_events=[older, newer])

        assert driver_ids == ["driver_b"]
        assert driver_to_constructor == {"driver_b": "mercedes"}

    def test_ignores_sprint_races_only_a_field_race_counts_as_the_current_lineup(self):
        sprint = _sprint_event("2024-2-sprint", "2024-03-09", [_participant("sprint_only_driver")], status="completed")
        field = _field_event("2024-1", "2024-03-02", [_participant("driver_a")], status="completed")
        storage = MagicMock()

        driver_ids, _ = live_features.current_roster(storage, "f1", all_events=[sprint, field])

        assert driver_ids == ["driver_a"]

    def test_no_completed_field_race_returns_an_empty_roster(self):
        storage = MagicMock()
        driver_ids, driver_to_constructor = live_features.current_roster(storage, "f1", all_events=[])
        assert driver_ids == []
        assert driver_to_constructor == {}


class TestBuildLiveSprintFeatures:
    def test_raises_malformed_for_wrong_event_type(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "field"}
        with pytest.raises(live_features.MalformedEventError):
            live_features.build_live_sprint_features(storage, "f1", "2024-2")

    def test_returns_a_row_per_participant_no_constructor_rows(self):
        event = _sprint_event("2024-5-sprint", "2024-04-20", [_participant("max_verstappen")])
        storage = MagicMock()
        storage.get_event.return_value = event
        storage.get_all_events.return_value = [event]

        built = live_features.build_live_sprint_features(storage, "f1", "2024-5-sprint")

        assert set(built["driver_rows"]) == {"max_verstappen"}
        assert "constructor_rows" not in built

    def test_sprint_history_is_tracked_separately_from_field_history(self):
        field_history = _field_event("2024-4", "2024-04-19", [_participant("max_verstappen", finish_position=1, status="finished")])
        sprint_event = _sprint_event("2024-5-sprint", "2024-04-20", [_participant("max_verstappen")])
        storage = MagicMock()
        storage.get_event.return_value = sprint_event
        storage.get_all_events.return_value = [field_history, sprint_event]

        built = live_features.build_live_sprint_features(storage, "f1", "2024-5-sprint")

        # The main race's own history never leaks into the Sprint's rolling stats.
        assert built["driver_rows"]["max_verstappen"]["starts"] == 0


class TestBuildProjectedFieldFeatures:
    def test_builds_a_row_per_driver_id_regardless_of_stored_participants(self):
        future_race = _field_event("2024-9", "2024-06-01", [], status="scheduled")
        storage = MagicMock()

        rows = live_features.build_projected_field_features(
            storage, "f1", future_race, ["max_verstappen", "lewis_hamilton"],
            {"max_verstappen": "red_bull", "lewis_hamilton": "mercedes"}, all_events=[],
        )

        assert set(rows) == {"max_verstappen", "lewis_hamilton"}
        assert rows["max_verstappen"]["constructor_entity_id"] == "red_bull"
