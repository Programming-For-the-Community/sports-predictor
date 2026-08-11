"""
Unit tests for ncaafb/predict/handler.py -- a pure background compute
worker (see that module's own docstring), dispatching on `detail-type`
only. No API-Gateway-shaped routing lives here anymore -- GET
/ncaafb/predictions/events/{event_id} and .../players/{entity_id} moved
to predict-read/handler.py (see test_predict_read_handler.py's own
TestPredictionRoutes); ScheduledSeasonProjection is covered separately in
test_season_projection.py. The ncaafb_predict module is registered in
sys.modules by conftest.py, which also resets its singletons around every
test in this directory.
"""
from unittest.mock import patch

import ncaafb_predict
import event_prediction


class TestComputeAndCacheDispatch:
    def test_event_route_calls_compute_and_cache_event(self):
        with patch.object(ncaafb_predict, "_get_storage"), \
             patch.object(ncaafb_predict, "_get_model_bucket"), \
             patch.object(ncaafb_predict, "_get_predictions_table"), \
             patch.object(event_prediction, "compute_and_cache_event") as mock_compute:
            response = ncaafb_predict.lambda_handler(
                {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": "401520281"}, None,
            )

        assert response == {"status": "ok"}
        mock_compute.assert_called_once()
        assert mock_compute.call_args.args[-1] == "401520281"

    def test_player_prop_route_calls_compute_and_cache_player_prop(self):
        with patch.object(ncaafb_predict, "_get_storage"), \
             patch.object(ncaafb_predict, "_get_model_bucket"), \
             patch.object(ncaafb_predict, "_get_predictions_table"), \
             patch.object(event_prediction, "compute_and_cache_player_prop") as mock_compute:
            response = ncaafb_predict.lambda_handler(
                {
                    "detail-type": "ComputeAndCachePrediction", "route": "player_prop",
                    "event_id": "401520281", "entity_id": "101", "stat": "passing_yards",
                }, None,
            )

        assert response == {"status": "ok"}
        mock_compute.assert_called_once()
        assert mock_compute.call_args.args[-3:] == ("401520281", "101", "passing_yards")

    def test_unrecognized_invocation_shape_does_not_raise(self):
        response = ncaafb_predict.lambda_handler({"resource": "/ncaafb/unknown"}, None)

        assert response["status"] == "error"
