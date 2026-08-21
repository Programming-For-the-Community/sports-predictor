"""
Unit tests for ncaambb/predict/handler.py -- a pure background compute
worker (see that module's own docstring), dispatching on `detail-type`
only. No API-Gateway-shaped routing lives here -- GET
/ncaambb/predictions/events/{...} lives in predict-read/handler.py (see
test_predict_read_handler.py's own TestPredictionRoutes). No
ScheduledSeasonProjection branch yet -- season_projection.py doesn't
exist until step 8 (see handler.py's own docstring). The ncaambb_predict
module is registered in sys.modules by conftest.py, which also resets its
singletons around every test in this directory.
"""
from unittest.mock import patch

import event_prediction
import ncaambb_predict


class TestWarmup:
    def test_warmup_ping_touches_singletons_and_skips_routing(self):
        with patch.object(ncaambb_predict, "_get_storage") as mock_storage, \
             patch.object(ncaambb_predict, "_get_model_bucket") as mock_bucket, \
             patch.object(ncaambb_predict, "_get_predictions_table") as mock_table:
            response = ncaambb_predict.lambda_handler({"warmup": True}, None)

        assert response == {"status": "warm"}
        mock_storage.assert_called_once()
        mock_bucket.assert_called_once()
        mock_table.assert_called_once()


class TestComputeAndCacheDispatch:
    def test_event_route_calls_compute_and_cache_event(self):
        with patch.object(ncaambb_predict, "_get_storage"), \
             patch.object(ncaambb_predict, "_get_model_bucket"), \
             patch.object(ncaambb_predict, "_get_predictions_table"), \
             patch.object(event_prediction, "compute_and_cache_event") as mock_compute:
            response = ncaambb_predict.lambda_handler(
                {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": "401705127"}, None,
            )

        assert response == {"status": "ok"}
        mock_compute.assert_called_once()
        assert mock_compute.call_args.args[-1] == "401705127"

    def test_player_prop_route_calls_compute_and_cache_player_prop(self):
        with patch.object(ncaambb_predict, "_get_storage"), \
             patch.object(ncaambb_predict, "_get_model_bucket"), \
             patch.object(ncaambb_predict, "_get_predictions_table"), \
             patch.object(event_prediction, "compute_and_cache_player_prop") as mock_compute:
            response = ncaambb_predict.lambda_handler(
                {
                    "detail-type": "ComputeAndCachePrediction", "route": "player_prop",
                    "event_id": "401705127", "entity_id": "101", "stat": "points",
                }, None,
            )

        assert response == {"status": "ok"}
        mock_compute.assert_called_once()
        assert mock_compute.call_args.args[-3:] == ("401705127", "101", "points")

    def test_unrecognized_invocation_shape_does_not_raise(self):
        response = ncaambb_predict.lambda_handler({"resource": "/ncaambb/unknown"}, None)

        assert response["status"] == "error"
