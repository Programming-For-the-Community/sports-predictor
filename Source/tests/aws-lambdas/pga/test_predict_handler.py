"""
Unit tests for pga/predict/handler.py -- a pure background compute worker
(see that module's own docstring), dispatching on detail-type/route only.
No API-Gateway-shaped routing lives here -- GET /pga/predictions/events/
{...} lives in predict-read/handler.py (see test_predict_read_handler.py's
own TestPredictionRoute). The pga_predict module is registered in
sys.modules by conftest.py, which also resets its singletons around every
test in this directory.
"""
from unittest.mock import patch

import event_prediction
import pga_predict
import season_projection


class TestScheduledSeasonProjectionDispatch:
    def test_dispatches_to_season_projections_own_run_scheduled(self):
        with patch.object(pga_predict, "_get_storage"), \
             patch.object(pga_predict, "_get_model_bucket"), \
             patch.object(pga_predict, "_get_predictions_table"), \
             patch.object(season_projection, "run_scheduled", return_value={"sport": "pga", "season": 2026}) as mock_run:
            response = pga_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"sport": "pga", "season": 2026}
        mock_run.assert_called_once()


class TestWarmup:
    def test_warmup_ping_touches_singletons_and_skips_routing(self):
        with patch.object(pga_predict, "_get_storage") as mock_storage, \
             patch.object(pga_predict, "_get_model_bucket") as mock_bucket, \
             patch.object(pga_predict, "_get_predictions_table") as mock_table:
            response = pga_predict.lambda_handler({"warmup": True}, None)

        assert response == {"status": "warm"}
        mock_storage.assert_called_once()
        mock_bucket.assert_called_once()
        mock_table.assert_called_once()


class TestComputeAndCacheDispatch:
    def test_event_route_calls_compute_and_cache_event(self):
        with patch.object(pga_predict, "_get_storage"), \
             patch.object(pga_predict, "_get_model_bucket"), \
             patch.object(pga_predict, "_get_predictions_table"), \
             patch.object(event_prediction, "compute_and_cache_event") as mock_compute:
            response = pga_predict.lambda_handler(
                {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": "401811963"}, None,
            )

        assert response == {"status": "ok"}
        mock_compute.assert_called_once()
        assert mock_compute.call_args.args[-1] == "401811963"

    def test_unrecognized_route_is_treated_as_unrecognized_invocation(self):
        response = pga_predict.lambda_handler(
            {"detail-type": "ComputeAndCachePrediction", "route": "player_prop", "event_id": "1"}, None,
        )

        assert response["status"] == "error"

    def test_unrecognized_invocation_shape_does_not_raise(self):
        response = pga_predict.lambda_handler({"resource": "/pga/unknown"}, None)

        assert response["status"] == "error"


class TestLazySingletons:
    def test_each_client_is_built_once_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "models")
        monkeypatch.setenv("PREDICTIONS_TABLE_NAME", "predictions")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        with patch.object(pga_predict, "FeatureStorage") as storage_cls,              patch.object(pga_predict, "S3Manager") as s3_cls,              patch.object(pga_predict, "DynamoDBTable") as table_cls:
            for _ in range(2):
                assert pga_predict._get_storage() is storage_cls.return_value
                assert pga_predict._get_model_bucket() is s3_cls.return_value
                assert pga_predict._get_predictions_table() is table_cls.return_value

        storage_cls.assert_called_once_with()
        s3_cls.assert_called_once_with("models", region="us-east-1")
        table_cls.assert_called_once_with("predictions", region="us-east-1")
