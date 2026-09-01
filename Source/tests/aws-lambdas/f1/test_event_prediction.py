"""
Unit tests for f1/predict/event_prediction.py. live_features/model_loader
are mocked out -- this exercises predict_field_event/predict_sprint_event/
predict_event's own scoring/sorting/dispatch logic, not real inference.
"""
from unittest.mock import MagicMock, patch

import event_prediction
import model_loader


def _built_field_features(driver_rows=None, constructor_rows=None):
    return {
        "event": {
            "event_key": "SPORT#F1#EVENT#2024-1", "event_id": "2024-1", "event_type": "field",
            "race_name": "Bahrain Grand Prix", "status": "scheduled", "circuit_id": "bahrain",
            "season": 2024, "week": 1,
            "participants": [{"entity_id": "max_verstappen", "result": {}}],
        },
        "driver_rows": driver_rows if driver_rows is not None else {"max_verstappen": {"constructor_entity_id": "red_bull"}},
        "constructor_rows": constructor_rows if constructor_rows is not None else {"red_bull": {}},
    }


def _built_sprint_features(driver_rows=None):
    return {
        "event": {
            "event_key": "SPORT#F1#EVENT#2024-5-sprint", "event_id": "2024-5-sprint", "event_type": "sprint",
            "race_name": "Chinese Grand Prix", "status": "scheduled", "circuit_id": "shanghai",
            "season": 2024, "week": 5,
            "participants": [{"entity_id": "max_verstappen", "result": {}}],
        },
        "driver_rows": driver_rows if driver_rows is not None else {"max_verstappen": {"constructor_entity_id": "red_bull"}},
    }


class TestScore:
    def test_returns_none_when_no_promoted_model_exists(self):
        with patch.object(event_prediction.model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError()):
            result = event_prediction._score({}, MagicMock(), MagicMock(), "K", "win-probability", {}, "DRIVER#a")
        assert result is None

    def test_scores_and_records_the_prediction(self):
        predictions_table = MagicMock()
        with patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 2})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.42):
            result = event_prediction._score({}, MagicMock(), predictions_table, "K", "win-probability", {}, "DRIVER#a")

        assert result == {"value": 0.42, "model_version": 2}
        predictions_table.put_item.assert_called_once()
        assert predictions_table.put_item.call_args.args[0]["model_key"] == "MODEL#win-probability#v2#DRIVER#a"


class TestPredictFieldEvent:
    def test_scores_every_driver_and_constructor(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Max Verstappen"}
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features()), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, MagicMock(), MagicMock(), "2024-1")

        assert result["event_type"] == "field"
        assert len(result["field"]) == 1
        assert result["field"][0]["predictions"]["win_probability"]["value"] == 0.5
        assert len(result["constructors"]) == 1
        assert result["constructors"][0]["predictions"]["win_probability"]["value"] == 0.5

    def test_a_missing_model_is_simply_absent_from_predictions(self):
        storage = MagicMock()
        storage.get_entity.return_value = None

        def _load(s3, sport, model_name):
            if model_name == "dnf-probability":
                raise model_loader.NoPromotedModelError()
            return MagicMock(), {"version": 1}

        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features()), \
             patch.object(event_prediction.model_loader, "load_current_model", side_effect=_load), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, MagicMock(), MagicMock(), "2024-1")

        assert "dnf_probability" not in result["field"][0]["predictions"]
        assert "win_probability" in result["field"][0]["predictions"]

    def test_actual_result_attached_once_qualifying_or_race_lands(self):
        built = _built_field_features()
        built["event"]["participants"][0]["result"] = {"status": "finished", "finish_position": 1, "qualifying": {"position": 1}}
        storage = MagicMock()
        storage.get_entity.return_value = None

        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=built), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, MagicMock(), MagicMock(), "2024-1")

        assert result["field"][0]["actual"]["finish_position"] == 1

    def test_no_actual_key_before_qualifying_or_the_race_has_happened(self):
        storage = MagicMock()
        storage.get_entity.return_value = None

        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features()), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, MagicMock(), MagicMock(), "2024-1")

        assert "actual" not in result["field"][0]

    def test_constructor_name_is_the_constructors_own_real_name_not_the_drivers(self):
        built = _built_field_features()
        storage = MagicMock()

        def _get_entity(sport, entity_id, entity_type):
            if entity_type == "team":
                return {"name": "Red Bull"}
            return {"name": "Max Verstappen"}
        storage.get_entity.side_effect = _get_entity

        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=built), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, MagicMock(), MagicMock(), "2024-1")

        entry = result["field"][0]
        assert entry["name"] == "Max Verstappen"
        assert entry["constructor_entity_id"] == "red_bull"
        assert entry["constructor_name"] == "Red Bull"

    def test_constructor_name_is_none_when_the_constructor_has_no_entity(self):
        built = _built_field_features()
        storage = MagicMock()
        storage.get_entity.return_value = None

        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=built), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, MagicMock(), MagicMock(), "2024-1")

        assert result["field"][0]["constructor_name"] is None

    def test_field_sorted_ascending_by_projected_finish_position(self):
        built = _built_field_features(driver_rows={
            "driver_a": {"constructor_entity_id": "red_bull"}, "driver_b": {"constructor_entity_id": "mercedes"},
        })
        storage = MagicMock()
        storage.get_entity.return_value = None

        # FIELD_EVENT_MODELS iterates win/podium/finish/dnf/qualifying per
        # driver (5 calls each, driver_a then driver_b), then 1 constructor
        # call -- only the 3rd value in each driver's own block of 5
        # (projected_finish_position) is varied; driver_a's is worse (3.0
        # vs driver_b's 1.0), so driver_b must sort first.
        predict_values = [1.0, 1.0, 3.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=built), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", side_effect=predict_values):
            result = event_prediction.predict_field_event(storage, MagicMock(), MagicMock(), "2024-1")

        assert result["field"][0]["entity_id"] == "driver_b"

    def test_qualifying_positions_get_a_unique_rank_even_when_predicted_values_round_the_same(self):
        built = _built_field_features(driver_rows={
            "driver_a": {"constructor_entity_id": "red_bull"}, "driver_b": {"constructor_entity_id": "mercedes"},
        })
        storage = MagicMock()
        storage.get_entity.return_value = None

        # 5th value in each driver's own block of 5 is projected_qualifying_
        # position -- 3.4 and 3.2 both round to 3, which used to render as
        # a duplicate "P3" for both drivers before rank existed.
        predict_values = [1.0, 1.0, 1.0, 1.0, 3.4, 1.0, 1.0, 1.0, 1.0, 3.2, 1.0]
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=built), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", side_effect=predict_values):
            result = event_prediction.predict_field_event(storage, MagicMock(), MagicMock(), "2024-1")

        by_entity = {entry["entity_id"]: entry for entry in result["field"]}
        assert by_entity["driver_b"]["predictions"]["projected_qualifying_position"]["rank"] == 1  # 3.2, better
        assert by_entity["driver_a"]["predictions"]["projected_qualifying_position"]["rank"] == 2  # 3.4, worse


class TestAssignQualifyingRanks:
    def _entry(self, entity_id, qualifying_value=None):
        predictions = {}
        if qualifying_value is not None:
            predictions["projected_qualifying_position"] = {"value": qualifying_value}
        return {"entity_id": entity_id, "predictions": predictions}

    def test_ranks_ascending_by_value(self):
        field = [self._entry("a", 5.0), self._entry("b", 1.0), self._entry("c", 3.0)]

        event_prediction._assign_qualifying_ranks(field)

        assert {e["entity_id"]: e["predictions"]["projected_qualifying_position"]["rank"] for e in field} == {
            "b": 1, "c": 2, "a": 3,
        }

    def test_ties_broken_deterministically_by_entity_id(self):
        field = [self._entry("zed", 2.0), self._entry("alice", 2.0)]

        event_prediction._assign_qualifying_ranks(field)

        # Same predicted value -- "alice" sorts first alphabetically, so it
        # gets the better (lower) rank, every time this runs.
        assert field[0]["predictions"]["projected_qualifying_position"]["rank"] == 2  # zed
        assert field[1]["predictions"]["projected_qualifying_position"]["rank"] == 1  # alice

    def test_entries_with_no_qualifying_model_are_skipped_not_errored(self):
        field = [self._entry("a", 2.0), self._entry("b", qualifying_value=None)]

        event_prediction._assign_qualifying_ranks(field)

        assert field[0]["predictions"]["projected_qualifying_position"]["rank"] == 1
        assert "projected_qualifying_position" not in field[1]["predictions"]


class TestFieldSortKey:
    def _entry(self, **predictions):
        return {"predictions": predictions}

    def test_sorts_by_projected_finish_position_ascending_when_present(self):
        worse = self._entry(projected_finish_position={"value": 5.0})
        better = self._entry(projected_finish_position={"value": 2.0})
        assert event_prediction._field_sort_key(better) < event_prediction._field_sort_key(worse)

    def test_falls_back_to_projected_grid_position_when_no_finish_position_model(self):
        # Sprint events have no projected_finish_position at all -- real
        # regression: the old fallback (win_probability) meant a sprint's
        # own field was never actually ordered by projected grid at all.
        worse = self._entry(projected_grid_position={"value": 8.0}, win_probability={"value": 0.9})
        better = self._entry(projected_grid_position={"value": 1.0}, win_probability={"value": 0.1})
        assert event_prediction._field_sort_key(better) < event_prediction._field_sort_key(worse)

    def test_falls_back_to_win_probability_descending_when_neither_position_model_exists(self):
        worse = self._entry(win_probability={"value": 0.1})
        better = self._entry(win_probability={"value": 0.9})
        assert event_prediction._field_sort_key(better) < event_prediction._field_sort_key(worse)


class TestPredictSprintEvent:
    def test_scores_every_driver_no_constructors(self):
        storage = MagicMock()
        storage.get_entity.return_value = None
        with patch.object(event_prediction.live_features, "build_live_sprint_features", return_value=_built_sprint_features()), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_sprint_event(storage, MagicMock(), MagicMock(), "2024-5-sprint")

        assert result["event_type"] == "sprint"
        assert "constructors" not in result
        assert result["field"][0]["predictions"]["win_probability"]["value"] == 0.5


class TestPredictEventDispatch:
    def test_dispatches_field_to_predict_field_event(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "field"}
        with patch.object(event_prediction, "predict_field_event", return_value={"ok": "field"}) as mock_field:
            result = event_prediction.predict_event(storage, MagicMock(), MagicMock(), "2024-1")
        assert result == {"ok": "field"}
        mock_field.assert_called_once()

    def test_dispatches_sprint_to_predict_sprint_event(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "sprint"}
        with patch.object(event_prediction, "predict_sprint_event", return_value={"ok": "sprint"}) as mock_sprint:
            result = event_prediction.predict_event(storage, MagicMock(), MagicMock(), "2024-5-sprint")
        assert result == {"ok": "sprint"}
        mock_sprint.assert_called_once()

    def test_raises_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        try:
            event_prediction.predict_event(storage, MagicMock(), MagicMock(), "missing")
            assert False, "expected EventNotFoundError"
        except event_prediction.live_features.EventNotFoundError:
            pass

    def test_raises_malformed_event_for_unknown_type(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "qualifying"}
        try:
            event_prediction.predict_event(storage, MagicMock(), MagicMock(), "2024-1")
            assert False, "expected MalformedEventError"
        except event_prediction.live_features.MalformedEventError:
            pass


class TestComputeAndCacheEvent:
    def test_caches_a_recognized_error_and_clears_in_progress(self):
        storage = MagicMock()
        s3 = MagicMock()
        with patch.object(event_prediction, "predict_event", side_effect=event_prediction.live_features.EventNotFoundError("nope")), \
             patch.object(event_prediction.prediction_cache, "put_error_cached") as mock_put_error, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as mock_clear:
            event_prediction.compute_and_cache_event(storage, s3, MagicMock(), "missing")

        mock_put_error.assert_called_once()
        mock_clear.assert_called_once()

    def test_caches_a_successful_result(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "field", "participants": []}
        s3 = MagicMock()
        result = {"event_type": "field", "status": "scheduled"}
        with patch.object(event_prediction, "predict_event", return_value=result), \
             patch.object(event_prediction.prediction_cache, "current_model_versions", return_value={}), \
             patch.object(event_prediction.prediction_cache, "put_cached") as mock_put_cached, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress"):
            event_prediction.compute_and_cache_event(storage, s3, MagicMock(), "2024-1")

        mock_put_cached.assert_called_once()
