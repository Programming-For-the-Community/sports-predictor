"""
Unit tests for the NFL read-only serving Lambda. library.serving.nfl_reads
is the real module (already covered by its own test file,
Source/tests/library/serving/test_nfl_reads.py) -- FeatureStorage,
S3Manager, and the predictions DynamoDBTable are mocked here, since this
file's only job is verifying lambda_handler's own routing, response
shaping, and error handling.

The nfl_predict_read module is registered in sys.modules by conftest.py.
"""
import json
from unittest.mock import MagicMock

import pytest

import nfl_predict_read


@pytest.fixture(autouse=True)
def reset_singletons():
    nfl_predict_read._storage = None
    nfl_predict_read._model_bucket = None
    nfl_predict_read._predictions_table = None
    yield
    nfl_predict_read._storage = None
    nfl_predict_read._model_bucket = None
    nfl_predict_read._predictions_table = None


def _api_event(resource: str, query_params: dict | None = None) -> dict:
    return {"resource": resource, "queryStringParameters": query_params or {}}


class TestEventsRoute:
    def test_returns_events_and_defaults_to_scheduled_status(self):
        nfl_predict_read._storage = MagicMock()
        nfl_predict_read._storage.get_all_events.return_value = []
        nfl_predict_read._predictions_table = MagicMock()

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/events"), None)

        assert response["statusCode"] == 200
        nfl_predict_read._storage.get_all_events.assert_called_once_with("nfl", status="scheduled")

    def test_passes_the_requested_status_through(self):
        nfl_predict_read._storage = MagicMock()
        nfl_predict_read._storage.get_all_events.return_value = []
        nfl_predict_read._predictions_table = MagicMock()

        nfl_predict_read.lambda_handler(_api_event("/nfl/events", {"status": "completed"}), None)

        nfl_predict_read._storage.get_all_events.assert_called_once_with("nfl", status="completed")

    def test_response_body_matches_list_events_shape(self):
        nfl_predict_read._storage = MagicMock()
        nfl_predict_read._storage.get_all_events.return_value = [
            {
                "event_id": "401547417", "event_date": "2025-09-28", "status": "scheduled",
                "season": 2025, "season_type": 2, "week": 4,
                "participants": [{"entity_id": "12", "role": "home"}, {"entity_id": "24", "role": "away"}],
            },
        ]
        nfl_predict_read._predictions_table = MagicMock()

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/events"), None)

        body = json.loads(response["body"])
        assert body["sport"] == "nfl"
        assert body["events"][0]["event_id"] == "401547417"


class TestModelsRoute:
    def test_returns_a_model_card_summary_per_current_model(self):
        nfl_predict_read._model_bucket = MagicMock()
        nfl_predict_read._model_bucket.list_keys.return_value = ["nfl/win-probability/current.json"]
        nfl_predict_read._model_bucket.object_exists.return_value = True
        nfl_predict_read._model_bucket.get_json.side_effect = [
            {"version": 6},
            {
                "model_name": "win-probability", "algorithm": "xgboost", "version": 6,
                "trained_at": "2026-01-01T00:00:00Z", "accuracy": 0.63, "log_loss": 0.65,
                "feature_importances": {},
            },
        ]

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/models"), None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["models"][0]["model_name"] == "win-probability"


class TestSeasonRoute:
    def test_returns_the_cached_projection(self):
        nfl_predict_read._model_bucket = MagicMock()
        nfl_predict_read._model_bucket.object_exists.return_value = True
        nfl_predict_read._model_bucket.get_json.return_value = {"sport": "nfl", "season": 2025, "standings": []}

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/season"), None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["season"] == 2025
        nfl_predict_read._model_bucket.get_json.assert_called_once_with("season-projections/nfl/latest.json")

    def test_returns_503_when_the_scheduled_job_hasnt_written_one_yet(self):
        nfl_predict_read._model_bucket = MagicMock()
        nfl_predict_read._model_bucket.object_exists.return_value = False

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/season"), None)

        assert response["statusCode"] == 503


class TestRouting:
    def test_unknown_resource_is_a_404(self):
        response = nfl_predict_read.lambda_handler(_api_event("/nfl/predictions/events/{event_id}"), None)

        assert response["statusCode"] == 404

    def test_every_response_carries_cors_headers(self):
        response = nfl_predict_read.lambda_handler(_api_event("/nfl/something-else"), None)

        assert response["headers"]["Access-Control-Allow-Origin"] == "*"

    def test_unexpected_exception_is_a_500_not_a_raw_502(self):
        nfl_predict_read._storage = MagicMock()
        nfl_predict_read._storage.get_all_events.side_effect = RuntimeError("boom")

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/events"), None)

        assert response["statusCode"] == 500
