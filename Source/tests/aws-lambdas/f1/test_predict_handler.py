"""
Unit tests for f1/predict/handler.py -- a pure background compute worker
(see that module's own docstring), dispatching on detail-type/route only.
The f1_predict module is registered in sys.modules by conftest.py, which
also resets its singletons around every test in this directory.
"""
from unittest.mock import patch

import event_prediction
import f1_predict
import season_projection


class TestScheduledSeasonProjectionDispatch:
    def test_dispatches_to_season_projections_own_run_scheduled(self):
        with patch.object(f1_predict, "_get_storage"), \
             patch.object(f1_predict, "_get_model_bucket"), \
             patch.object(f1_predict, "_get_predictions_table"), \
             patch.object(season_projection, "run_scheduled", return_value={"sport": "f1", "season": 2026}) as mock_run:
            response = f1_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"sport": "f1", "season": 2026}
        mock_run.assert_called_once()


class TestWarmup:
    def test_warmup_ping_touches_singletons_and_skips_routing(self):
        with patch.object(f1_predict, "_get_storage") as mock_storage, \
             patch.object(f1_predict, "_get_model_bucket") as mock_bucket, \
             patch.object(f1_predict, "_get_predictions_table") as mock_table:
            response = f1_predict.lambda_handler({"warmup": True}, None)

        assert response == {"status": "warm"}
        mock_storage.assert_called_once()
        mock_bucket.assert_called_once()
        mock_table.assert_called_once()


class TestComputeAndCacheDispatch:
    def test_event_route_calls_compute_and_cache_event(self):
        with patch.object(f1_predict, "_get_storage"), \
             patch.object(f1_predict, "_get_model_bucket"), \
             patch.object(f1_predict, "_get_predictions_table"), \
             patch.object(event_prediction, "compute_and_cache_event") as mock_compute:
            response = f1_predict.lambda_handler(
                {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": "2026-5"}, None,
            )

        assert response == {"status": "ok"}
        mock_compute.assert_called_once()
        assert mock_compute.call_args.args[-1] == "2026-5"

    def test_unrecognized_route_is_treated_as_unrecognized_invocation(self):
        response = f1_predict.lambda_handler(
            {"detail-type": "ComputeAndCachePrediction", "route": "player_prop", "event_id": "1"}, None,
        )

        assert response["status"] == "error"

    def test_unrecognized_invocation_shape_does_not_raise(self):
        response = f1_predict.lambda_handler({"resource": "/f1/unknown"}, None)

        assert response["status"] == "error"
