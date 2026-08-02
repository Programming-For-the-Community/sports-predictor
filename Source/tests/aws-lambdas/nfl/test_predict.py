"""
Unit tests for the NFL inference Lambda handler. live_features and
model_loader are the real modules (already covered by their own test
files, test_live_features.py/test_model_loader.py) -- FeatureStorage,
S3Manager, and the predictions DynamoDBTable are mocked here, since this
file's only job is verifying lambda_handler's own routing, response
shaping, and error-to-status-code mapping.

The nfl_predict module is registered in sys.modules by conftest.py.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import live_features
import model_loader
import nfl_predict


@pytest.fixture(autouse=True)
def reset_singletons():
    """Clear the module-level singletons before and after each test, same
    reasoning as nfl_normalize's reset_storage fixture -- otherwise a mock
    installed by one test would leak into the next."""
    nfl_predict._storage = None
    nfl_predict._model_bucket = None
    nfl_predict._predictions_table = None
    yield
    nfl_predict._storage = None
    nfl_predict._model_bucket = None
    nfl_predict._predictions_table = None


def _api_event(resource: str, path_params: dict | None = None, query_params: dict | None = None) -> dict:
    return {
        "resource": resource,
        "pathParameters": path_params or {},
        "queryStringParameters": query_params or {},
    }


def _model_card(version: int) -> dict:
    return {"version": version, "feature_columns": []}


class TestEventOutcomeRoute:
    def test_returns_all_four_event_level_predictions_from_one_feature_row(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1550}) as build, \
             patch.object(model_loader, "load_current_model", side_effect=[
                 (MagicMock(), _model_card(1)),
                 (MagicMock(), _model_card(2)),
                 (MagicMock(), _model_card(3)),
                 (MagicMock(), _model_card(4)),
             ]), \
             patch.object(model_loader, "predict", side_effect=[0.62, 3.2, 24.1, 20.9]):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["event_key"] == "SPORT#NFL#EVENT#401547417"
        assert body["predictions"]["win_probability"] == {"home_win_probability": 0.62, "model_version": 1}
        assert body["predictions"]["margin"] == {"value": 3.2, "model_version": 2}
        assert body["predictions"]["home_score"] == {"value": 24.1, "model_version": 3}
        assert body["predictions"]["away_score"] == {"value": 20.9, "model_version": 4}
        build.assert_called_once_with(nfl_predict._storage, "nfl", "SPORT#NFL#EVENT#401547417")

    def test_audits_one_predictions_table_write_per_model(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()

        with patch.object(live_features, "build_live_event_features", return_value={}), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=1.0):
            nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert nfl_predict._predictions_table.put_item.call_count == 4


class TestPlayerPropRoute:
    def test_returns_the_requested_stats_prediction(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()

        with patch.object(live_features, "build_live_player_features", return_value={"avg_passing_yards": 275}) as build, \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(5))) as load, \
             patch.object(model_loader, "predict", return_value=267.4):
            response = nfl_predict.lambda_handler(
                _api_event(
                    "/nfl/predictions/events/{event_id}/players/{entity_id}",
                    {"event_id": "401547417", "entity_id": "mahomes-patrick"},
                    {"stat": "passing_yards"},
                ),
                None,
            )

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["entity_key"] == "SPORT#NFL#ENTITY#mahomes-patrick"
        assert body["stat"] == "passing_yards"
        assert body["prediction"] == {"value": 267.4, "model_version": 5}
        build.assert_called_once_with(
            nfl_predict._storage, "nfl", "SPORT#NFL#EVENT#401547417", "mahomes-patrick",
        )
        load.assert_called_once_with(nfl_predict._model_bucket, "nfl", "player-prop-passing-yards")
        nfl_predict._predictions_table.put_item.assert_called_once()

    def test_missing_stat_query_param_is_a_400_not_a_crash(self):
        response = nfl_predict.lambda_handler(
            _api_event(
                "/nfl/predictions/events/{event_id}/players/{entity_id}",
                {"event_id": "401547417", "entity_id": "mahomes-patrick"},
            ),
            None,
        )

        assert response["statusCode"] == 400


class TestRouting:
    def test_unknown_resource_is_a_404(self):
        response = nfl_predict.lambda_handler(_api_event("/nfl/something-else"), None)

        assert response["statusCode"] == 404

    def test_every_response_carries_cors_headers(self):
        response = nfl_predict.lambda_handler(_api_event("/nfl/something-else"), None)

        assert response["headers"]["Access-Control-Allow-Origin"] == "*"


class TestErrorMapping:
    def test_event_not_found_is_a_404(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        with patch.object(live_features, "build_live_event_features", side_effect=live_features.EventNotFoundError("nope")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "missing"}), None,
            )

        assert response["statusCode"] == 404

    def test_malformed_event_is_a_422(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        with patch.object(live_features, "build_live_event_features", side_effect=live_features.MalformedEventError("bad")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 422

    def test_no_promoted_model_is_a_503(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        with patch.object(live_features, "build_live_event_features", return_value={}), \
             patch.object(model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError("none yet")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 503

    def test_unexpected_exception_is_a_500_not_a_raw_502(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        with patch.object(live_features, "build_live_event_features", side_effect=RuntimeError("boom")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 500
