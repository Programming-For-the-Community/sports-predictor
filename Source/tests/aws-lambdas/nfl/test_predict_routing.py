"""
Unit tests for nfl/predict/handler.py -- a background compute worker,
dispatching on `detail-type` only. GET /nfl/predictions/events/{event_id}
and .../players/{entity_id} are covered separately in test_predict_read.py;
ScheduledSeasonProjection is covered separately in
test_predict_season_simulation.py.

The nfl_predict module is registered in sys.modules by conftest.py, whose
reset_nfl_predict_singletons fixture (autouse) resets nfl_predict._storage/
_model_bucket/_predictions_table before and after every test here.
"""
from unittest.mock import patch

import event_prediction
import nfl_predict


class TestComputeAndCacheDispatch:
    def test_event_route_calls_compute_and_cache_event(self):
        with patch.object(nfl_predict, "_get_storage"), \
             patch.object(nfl_predict, "_get_model_bucket"), \
             patch.object(nfl_predict, "_get_predictions_table"), \
             patch.object(event_prediction, "compute_and_cache_event") as mock_compute:
            response = nfl_predict.lambda_handler(
                {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": "401547417"}, None,
            )

        assert response == {"status": "ok"}
        mock_compute.assert_called_once()
        assert mock_compute.call_args.args[-1] == "401547417"

    def test_player_prop_route_calls_compute_and_cache_player_prop(self):
        with patch.object(nfl_predict, "_get_storage"), \
             patch.object(nfl_predict, "_get_model_bucket"), \
             patch.object(nfl_predict, "_get_predictions_table"), \
             patch.object(event_prediction, "compute_and_cache_player_prop") as mock_compute:
            response = nfl_predict.lambda_handler(
                {
                    "detail-type": "ComputeAndCachePrediction", "route": "player_prop",
                    "event_id": "401547417", "entity_id": "mahomes-patrick", "stat": "passing_yards",
                }, None,
            )

        assert response == {"status": "ok"}
        mock_compute.assert_called_once()
        assert mock_compute.call_args.args[-3:] == ("401547417", "mahomes-patrick", "passing_yards")

    def test_unrecognized_invocation_shape_does_not_raise(self):
        response = nfl_predict.lambda_handler({"resource": "/nfl/unknown"}, None)

        assert response["status"] == "error"
