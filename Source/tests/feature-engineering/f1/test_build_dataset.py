"""
Unit tests for the F1 feature-engineering entrypoint's orchestration
logic -- the chronological driver-history walk (plus circuit/constructor
history) and Parquet assembly, and the constructor-grain dataset
builder. The actual feature math is tested in
tests/library/features/test_f1.py -- FeatureStorage/S3Manager are mocked
here so these tests only cover build_dataset.py's own wiring.
"""
import io
from unittest.mock import MagicMock

import pandas as pd

import build_dataset


def _participant(entity_id, constructor_entity_id="red_bull", finish_position=None, grid_position=None, points=0.0, status="finished", qualifying=None):
    return {
        "entity_id": entity_id,
        "constructor_entity_id": constructor_entity_id,
        "result": {
            "finish_position": finish_position, "grid_position": grid_position, "points": points, "status": status,
            "qualifying": qualifying,
        },
    }


def _qualifying(position, gap_to_pole_seconds=0.0):
    return {"position": position, "gap_to_pole_seconds": gap_to_pole_seconds}


def _event(event_key, event_date, participants, circuit_id="bahrain", event_type="field"):
    return {"event_key": event_key, "event_date": event_date, "event_type": event_type, "circuit_id": circuit_id, "participants": participants}


class TestWriteParquet:
    def test_empty_rows_returns_empty_bytes(self):
        assert build_dataset._write_parquet([]) == b""

    def test_writes_a_readable_parquet_frame(self):
        rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

        df = pd.read_parquet(io.BytesIO(build_dataset._write_parquet(rows)))

        assert list(df.columns) == ["a", "b"]
        assert df.iloc[1]["a"] == 3


class TestBuildDriverDataset:
    def _storage(self, events):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        return storage

    def test_one_row_per_participant_per_race(self):
        events = [_event("e1", "2024-01-01", [_participant("a"), _participant("b", constructor_entity_id="ferrari")])]
        storage = self._storage(events)

        rows = build_dataset.build_driver_dataset(storage, window=5)

        assert len(rows) == 2
        assert {r["entity_id"] for r in rows} == {"a", "b"}

    def test_non_field_events_are_excluded(self):
        events = [_event("e1", "2024-01-01", [_participant("a")], event_type="something_else")]
        storage = self._storage(events)

        rows = build_dataset.build_driver_dataset(storage, window=5)

        assert rows == []

    def test_a_second_race_sees_the_first_races_result_in_its_rolling_history(self):
        events = [
            _event("e1", "2024-01-01", [_participant("a", finish_position=1, points=25.0)]),
            _event("e2", "2024-01-08", [_participant("a", finish_position=5, points=10.0)]),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_driver_dataset(storage, window=5)
        second_row = next(r for r in rows if r["event_key"] == "e2")

        assert second_row["avg_finish_position"] == 1  # only race 1's result is in history
        assert second_row["starts"] == 1

    def test_a_races_own_field_never_sees_its_own_results_in_rolling_history(self):
        # Both drivers race in the SAME event -- neither should see the
        # other's (or their own) result from this same race.
        events = [_event("e1", "2024-01-01", [_participant("a", finish_position=1), _participant("b", finish_position=2)])]
        storage = self._storage(events)

        rows = build_dataset.build_driver_dataset(storage, window=5)

        assert all(r["starts"] == 0 for r in rows)

    def test_circuit_history_is_tracked_separately_from_overall_history(self):
        events = [
            _event("e1", "2024-01-01", [_participant("a", finish_position=1)], circuit_id="bahrain"),
            _event("e2", "2024-01-08", [_participant("a", finish_position=10)], circuit_id="jeddah"),
            _event("e3", "2025-01-01", [_participant("a", finish_position=5)], circuit_id="bahrain"),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_driver_dataset(storage, window=5)
        e3_row = next(r for r in rows if r["event_key"] == "e3")

        # e3 is at bahrain again -- circuit history should only include
        # e1 (also bahrain), not e2 (jeddah).
        assert e3_row["circuit_avg_finish_position"] == 1
        assert e3_row["circuit_starts"] == 1
        # overall history includes BOTH e1 and e2.
        assert e3_row["avg_finish_position"] == 5.5
        assert e3_row["starts"] == 2

    def test_constructor_history_pools_both_of_that_constructors_drivers(self):
        events = [
            _event("e1", "2024-01-01", [
                _participant("a", constructor_entity_id="red_bull", finish_position=1),
                _participant("b", constructor_entity_id="red_bull", finish_position=2),
            ]),
            _event("e2", "2024-01-08", [_participant("a", constructor_entity_id="red_bull", finish_position=8)]),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_driver_dataset(storage, window=5)
        e2_row = next(r for r in rows if r["event_key"] == "e2")

        # constructor history for e2 pools BOTH of e1's red_bull results.
        assert e2_row["constructor_avg_finish_position"] == 1.5
        assert e2_row["constructor_starts"] == 2

    def test_window_caps_the_rolling_history_length(self):
        events = [_event(f"e{i}", f"2024-{i:02d}-01", [_participant("a", finish_position=1)]) for i in range(1, 4)]
        events.append(_event("e4", "2024-04-01", [_participant("a", finish_position=20)]))
        storage = self._storage(events)

        rows = build_dataset.build_driver_dataset(storage, window=2)
        e4_row = next(r for r in rows if r["event_key"] == "e4")

        assert e4_row["starts"] == 2

    def test_qualifying_history_accumulates_across_races_and_feeds_the_next_rows_qualifying_block(self):
        events = [
            _event("e1", "2024-01-01", [_participant("a", qualifying=_qualifying(position=1))]),
            _event("e2", "2024-01-08", [_participant("a", qualifying=_qualifying(position=5))]),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_driver_dataset(storage, window=5)
        e2_row = next(r for r in rows if r["event_key"] == "e2")

        assert e2_row["qualifying_avg_qualifying_position"] == 1  # only e1's qualifying is in history
        assert e2_row["qualifying_qualifying_sessions"] == 1

    def test_a_race_with_no_qualifying_merged_yet_is_not_folded_into_the_qualifying_history(self):
        events = [
            _event("e1", "2024-01-01", [_participant("a", qualifying=None)]),  # not merged yet
            _event("e2", "2024-01-08", [_participant("a", qualifying=_qualifying(position=1))]),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_driver_dataset(storage, window=5)
        e2_row = next(r for r in rows if r["event_key"] == "e2")

        assert e2_row["qualifying_qualifying_sessions"] == 0

    def test_constructor_qualifying_history_pools_both_drivers(self):
        events = [
            _event("e1", "2024-01-01", [
                _participant("a", constructor_entity_id="red_bull", qualifying=_qualifying(position=1)),
                _participant("b", constructor_entity_id="red_bull", qualifying=_qualifying(position=3)),
            ]),
            _event("e2", "2024-01-08", [_participant("a", constructor_entity_id="red_bull")]),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_driver_dataset(storage, window=5)
        e2_row = next(r for r in rows if r["event_key"] == "e2")

        assert e2_row["constructor_qualifying_avg_qualifying_position"] == 2  # (1 + 3) / 2


class TestBuildConstructorDataset:
    def _storage(self, events):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        return storage

    def test_one_row_per_constructor_per_race(self):
        events = [_event("e1", "2024-01-01", [
            _participant("a", constructor_entity_id="red_bull"),
            _participant("b", constructor_entity_id="red_bull"),
            _participant("c", constructor_entity_id="ferrari"),
        ])]
        storage = self._storage(events)

        rows = build_dataset.build_constructor_dataset(storage, window=5)

        assert {r["entity_id"] for r in rows} == {"red_bull", "ferrari"}
        assert len(rows) == 2

    def test_a_driver_with_no_constructor_is_excluded(self):
        events = [_event("e1", "2024-01-01", [_participant("a", constructor_entity_id=None)])]
        storage = self._storage(events)

        rows = build_dataset.build_constructor_dataset(storage, window=5)

        assert rows == []

    def test_label_win_true_when_either_driver_wins(self):
        events = [_event("e1", "2024-01-01", [
            _participant("a", constructor_entity_id="red_bull", finish_position=1),
            _participant("b", constructor_entity_id="red_bull", finish_position=9),
        ])]
        storage = self._storage(events)

        rows = build_dataset.build_constructor_dataset(storage, window=5)

        assert rows[0]["label_win"] == 1

    def test_the_second_races_history_sums_both_drivers_prior_points(self):
        events = [
            _event("e1", "2024-01-01", [
                _participant("a", constructor_entity_id="red_bull", finish_position=1, points=25.0),
                _participant("b", constructor_entity_id="red_bull", finish_position=2, points=18.0),
            ]),
            _event("e2", "2024-01-08", [
                _participant("a", constructor_entity_id="red_bull", finish_position=3, points=15.0),
                _participant("b", constructor_entity_id="red_bull", finish_position=4, points=12.0),
            ]),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_constructor_dataset(storage, window=5)
        e2_row = next(r for r in rows if r["event_key"] == "e2")

        assert e2_row["avg_points"] == 43.0  # 25 + 18, summed not averaged


class TestBuildSprintDataset:
    def _storage(self, events):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        return storage

    def test_only_sprint_events_are_included(self):
        events = [
            _event("e1", "2024-01-01", [_participant("a")], event_type="field"),
            _event("e2", "2024-01-01", [_participant("a")], event_type="sprint"),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_sprint_dataset(storage, window=5)

        assert len(rows) == 1
        assert rows[0]["event_key"] == "e2"

    def test_sprint_history_is_tracked_separately_from_the_main_race(self):
        events = [
            _event("e1", "2024-01-01", [_participant("a", finish_position=1)], event_type="sprint"),
            _event("e2", "2024-01-08", [_participant("a", finish_position=5)], event_type="sprint"),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_sprint_dataset(storage, window=5)
        e2_row = next(r for r in rows if r["event_key"] == "e2")

        assert e2_row["avg_finish_position"] == 1
        assert e2_row["starts"] == 1

    def test_label_sprint_grid_position_comes_through(self):
        events = [_event("e1", "2024-01-01", [_participant("a", finish_position=3, grid_position=2)], event_type="sprint")]
        storage = self._storage(events)

        rows = build_dataset.build_sprint_dataset(storage, window=5)

        assert rows[0]["label_sprint_grid_position"] == 2

    def test_constructor_sprint_history_pools_both_drivers(self):
        events = [
            _event("e1", "2024-01-01", [
                _participant("a", constructor_entity_id="red_bull", finish_position=1),
                _participant("b", constructor_entity_id="red_bull", finish_position=2),
            ], event_type="sprint"),
            _event("e2", "2024-01-08", [_participant("a", constructor_entity_id="red_bull")], event_type="sprint"),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_sprint_dataset(storage, window=5)
        e2_row = next(r for r in rows if r["event_key"] == "e2")

        assert e2_row["constructor_avg_finish_position"] == 1.5
