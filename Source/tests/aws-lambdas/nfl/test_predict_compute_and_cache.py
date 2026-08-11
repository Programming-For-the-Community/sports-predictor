"""
Unit tests for event_prediction.compute_and_cache_event/
compute_and_cache_player_prop -- the ComputeAndCachePrediction background
worker predict-read/handler.py's own async invoke triggers on a
prediction-cache miss/stale-refresh (see predict/handler.py's own
docstring). predict_event/predict_player_prop themselves are mocked out
(already covered by test_predict_event_outcome.py/test_predict_leaders.py/
test_predict_player_props.py/etc.); this only checks the caching/error-
mapping wrapper around them.
"""
from unittest.mock import MagicMock, patch

import pytest

import event_prediction
import live_features
import model_loader


class TestComputeAndCacheEvent:
    RESULT = {
        "event_key": "SPORT#NFL#EVENT#1",
        "predictions": {
            "win_probability": {"home_win_probability": 0.6, "model_version": 1},
            "margin": {"value": 3.0, "model_version": 2},
            "home_score": {"value": 24.0, "model_version": 3},
            "away_score": {"value": 21.0, "model_version": 4},
        },
        "leaders": None,
    }

    def test_success_writes_the_result_to_the_cache_and_clears_the_claim(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_event.return_value = {"status": "completed"}
        with patch.object(event_prediction, "predict_event", return_value=self.RESULT), \
             patch.object(event_prediction.prediction_cache, "put_cached") as put_cached, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as clear_in_progress:
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, "1")

        cache_key, result, model_versions, event_status = put_cached.call_args.args[1:]
        assert result == self.RESULT
        assert model_versions == {"win_probability": 1, "margin": 2, "home_score": 3, "away_score": 4}
        assert event_status == "completed"
        clear_in_progress.assert_called_once()

    def test_event_not_found_writes_a_negative_cache_entry_instead_of_a_real_result(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction, "predict_event", side_effect=live_features.EventNotFoundError("nope")), \
             patch.object(event_prediction.prediction_cache, "put_error_cached") as put_error_cached, \
             patch.object(event_prediction.prediction_cache, "put_cached") as put_cached, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as clear_in_progress:
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, "1")

        put_error_cached.assert_called_once()
        assert put_error_cached.call_args.args[2] == "EventNotFoundError"
        put_cached.assert_not_called()
        clear_in_progress.assert_called_once()

    def test_an_unrecognized_exception_still_clears_the_claim_before_propagating(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction, "predict_event", side_effect=RuntimeError("boom")), \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as clear_in_progress, \
             pytest.raises(RuntimeError):
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, "1")

        clear_in_progress.assert_called_once()


class TestComputeAndCachePlayerProp:
    def test_success_writes_the_result_to_the_cache_and_clears_the_claim(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_event.return_value = {"status": "scheduled"}
        result = {"stat": "passing_yards", "prediction": {"value": 250.0, "model_version": 3}}
        with patch.object(event_prediction, "predict_player_prop", return_value=result), \
             patch.object(event_prediction.prediction_cache, "put_cached") as put_cached, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as clear_in_progress:
            event_prediction.compute_and_cache_player_prop(storage, s3, predictions_table, "1", "mahomes-patrick", "passing_yards")

        _, written_result, model_version, event_status = put_cached.call_args.args[1:]
        assert written_result == result
        assert model_version == 3
        assert event_status == "scheduled"
        clear_in_progress.assert_called_once()

    def test_no_promoted_model_writes_a_negative_cache_entry(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction, "predict_player_prop", side_effect=model_loader.NoPromotedModelError("none")), \
             patch.object(event_prediction.prediction_cache, "put_error_cached") as put_error_cached, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as clear_in_progress:
            event_prediction.compute_and_cache_player_prop(storage, s3, predictions_table, "1", "mahomes-patrick", "passing_yards")

        put_error_cached.assert_called_once()
        assert put_error_cached.call_args.args[2] == "NoPromotedModelError"
        clear_in_progress.assert_called_once()
