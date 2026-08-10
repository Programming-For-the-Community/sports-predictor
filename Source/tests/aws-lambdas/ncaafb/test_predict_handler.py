"""
Unit tests for ncaafb/predict/handler.py -- routing and error-to-status-code
mapping only; event_prediction's actual prediction logic is exercised in
test_event_prediction.py. The ncaafb_predict module is registered in
sys.modules by conftest.py, which also resets its singletons around every
test in this directory.
"""
import json
from unittest.mock import MagicMock, patch

import ncaafb_predict
import live_features
import model_loader


def _api_event(resource, path_params=None, query_params=None):
    return {"resource": resource, "pathParameters": path_params, "queryStringParameters": query_params}


class TestRouting:
    def test_predict_event_route(self):
        with patch.object(ncaafb_predict, "_get_storage"), \
             patch.object(ncaafb_predict, "_get_model_bucket"), \
             patch.object(ncaafb_predict, "_get_predictions_table"), \
             patch.object(ncaafb_predict.event_prediction, "predict_event", return_value={"ok": True}) as mock_predict:
            response = ncaafb_predict.lambda_handler(
                _api_event("/ncaafb/predictions/events/{event_id}", {"event_id": "401520281"}), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"ok": True}
        mock_predict.assert_called_once()
        assert mock_predict.call_args.args[-1] == "401520281"

    def test_predict_player_prop_route(self):
        with patch.object(ncaafb_predict, "_get_storage"), \
             patch.object(ncaafb_predict, "_get_model_bucket"), \
             patch.object(ncaafb_predict, "_get_predictions_table"), \
             patch.object(ncaafb_predict.event_prediction, "predict_player_prop", return_value={"ok": True}) as mock_predict:
            response = ncaafb_predict.lambda_handler(
                _api_event(
                    "/ncaafb/predictions/events/{event_id}/players/{entity_id}",
                    {"event_id": "401520281", "entity_id": "101"}, {"stat": "passing_yards"},
                ), None,
            )

        assert response["statusCode"] == 200
        mock_predict.assert_called_once()
        assert mock_predict.call_args.args[-1] == "passing_yards"

    def test_player_prop_route_missing_stat_query_param(self):
        response = ncaafb_predict.lambda_handler(
            _api_event(
                "/ncaafb/predictions/events/{event_id}/players/{entity_id}",
                {"event_id": "401520281", "entity_id": "101"}, {},
            ), None,
        )
        assert response["statusCode"] == 400

    def test_unknown_route_returns_404(self):
        response = ncaafb_predict.lambda_handler(_api_event("/ncaafb/unknown"), None)
        assert response["statusCode"] == 404


class TestErrorMapping:
    def test_event_not_found_returns_404(self):
        with patch.object(ncaafb_predict, "_get_storage"), \
             patch.object(ncaafb_predict, "_get_model_bucket"), \
             patch.object(ncaafb_predict, "_get_predictions_table"), \
             patch.object(ncaafb_predict.event_prediction, "predict_event", side_effect=live_features.EventNotFoundError("nope")):
            response = ncaafb_predict.lambda_handler(
                _api_event("/ncaafb/predictions/events/{event_id}", {"event_id": "1"}), None,
            )
        assert response["statusCode"] == 404

    def test_malformed_event_returns_422(self):
        with patch.object(ncaafb_predict, "_get_storage"), \
             patch.object(ncaafb_predict, "_get_model_bucket"), \
             patch.object(ncaafb_predict, "_get_predictions_table"), \
             patch.object(ncaafb_predict.event_prediction, "predict_event", side_effect=live_features.MalformedEventError("bad")):
            response = ncaafb_predict.lambda_handler(
                _api_event("/ncaafb/predictions/events/{event_id}", {"event_id": "1"}), None,
            )
        assert response["statusCode"] == 422

    def test_no_promoted_model_returns_503(self):
        with patch.object(ncaafb_predict, "_get_storage"), \
             patch.object(ncaafb_predict, "_get_model_bucket"), \
             patch.object(ncaafb_predict, "_get_predictions_table"), \
             patch.object(ncaafb_predict.event_prediction, "predict_event", side_effect=model_loader.NoPromotedModelError("none")):
            response = ncaafb_predict.lambda_handler(
                _api_event("/ncaafb/predictions/events/{event_id}", {"event_id": "1"}), None,
            )
        assert response["statusCode"] == 503

    def test_unhandled_exception_returns_500(self):
        with patch.object(ncaafb_predict, "_get_storage"), \
             patch.object(ncaafb_predict, "_get_model_bucket"), \
             patch.object(ncaafb_predict, "_get_predictions_table"), \
             patch.object(ncaafb_predict.event_prediction, "predict_event", side_effect=Exception("boom")):
            response = ncaafb_predict.lambda_handler(
                _api_event("/ncaafb/predictions/events/{event_id}", {"event_id": "1"}), None,
            )
        assert response["statusCode"] == 500


class TestCorsHeaders:
    def test_response_includes_cors_headers(self):
        response = ncaafb_predict.lambda_handler(_api_event("/ncaafb/unknown"), None)
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"
