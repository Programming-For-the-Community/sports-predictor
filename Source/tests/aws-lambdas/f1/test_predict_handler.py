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
from library.aws import serving_resources


class TestScheduledSeasonProjectionDispatch:
    def test_dispatches_to_season_projections_own_run_scheduled(self):
        with patch.object(f1_predict._resources, "storage"), \
             patch.object(f1_predict._resources, "model_bucket"), \
             patch.object(f1_predict._resources, "predictions_table"), \
             patch.object(season_projection, "run_scheduled", return_value={"sport": "f1", "season": 2026}) as mock_run:
            response = f1_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"sport": "f1", "season": 2026}
        mock_run.assert_called_once()


class TestWarmup:
    def test_warmup_ping_touches_singletons_and_skips_routing(self):
        with patch.object(f1_predict._resources, "storage") as mock_storage, \
             patch.object(f1_predict._resources, "model_bucket") as mock_bucket, \
             patch.object(f1_predict._resources, "predictions_table") as mock_table:
            response = f1_predict.lambda_handler({"warmup": True}, None)

        assert response == {"status": "warm"}
        mock_storage.assert_called_once()
        mock_bucket.assert_called_once()
        mock_table.assert_called_once()


class TestComputeAndCacheDispatch:
    def test_event_route_calls_compute_and_cache_event(self):
        with patch.object(f1_predict._resources, "storage"), \
             patch.object(f1_predict._resources, "model_bucket"), \
             patch.object(f1_predict._resources, "predictions_table"), \
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


class TestLazySingletons:
    def test_each_client_is_built_once_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "models")
        monkeypatch.setenv("PREDICTIONS_TABLE_NAME", "predictions")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        with patch.object(serving_resources, "FeatureStorage") as storage_cls,              patch.object(serving_resources, "S3Manager") as s3_cls,              patch.object(serving_resources, "DynamoDBTable") as table_cls:
            for _ in range(2):
                assert f1_predict._get_storage() is storage_cls.return_value
                assert f1_predict._get_model_bucket() is s3_cls.return_value
                assert f1_predict._get_predictions_table() is table_cls.return_value

        storage_cls.assert_called_once_with()
        s3_cls.assert_called_once_with("models", region="us-east-1")
        table_cls.assert_called_once_with("predictions", region="us-east-1")
