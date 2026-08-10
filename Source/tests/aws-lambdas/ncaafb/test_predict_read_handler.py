"""
Unit tests for ncaafb/predict-read/handler.py -- routing only; the actual
request-shaping logic (list_events/list_models) is exercised in
Source/tests/library/serving/test_ncaafb_reads.py. The
ncaafb_predict_read module is registered in sys.modules by conftest.py.
"""
import json
from unittest.mock import patch

import ncaafb_predict_read


def _api_event(resource, query_params=None):
    return {"resource": resource, "queryStringParameters": query_params}


class TestRouting:
    def test_events_route(self):
        with patch.object(ncaafb_predict_read, "_get_storage"), \
             patch.object(ncaafb_predict_read, "_get_predictions_table"), \
             patch.object(ncaafb_predict_read, "list_events", return_value={"sport": "ncaafb", "events": []}) as mock_list:
            response = ncaafb_predict_read.lambda_handler(_api_event("/ncaafb/events", {"status": "completed"}), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"sport": "ncaafb", "events": []}
        assert mock_list.call_args.args[-1] == "completed"

    def test_events_route_defaults_status_to_scheduled(self):
        with patch.object(ncaafb_predict_read, "_get_storage"), \
             patch.object(ncaafb_predict_read, "_get_predictions_table"), \
             patch.object(ncaafb_predict_read, "list_events", return_value={}) as mock_list:
            ncaafb_predict_read.lambda_handler(_api_event("/ncaafb/events"), None)

        assert mock_list.call_args.args[-1] == "scheduled"

    def test_models_route(self):
        with patch.object(ncaafb_predict_read, "_get_model_bucket"), \
             patch.object(ncaafb_predict_read, "list_models", return_value={"sport": "ncaafb", "models": []}):
            response = ncaafb_predict_read.lambda_handler(_api_event("/ncaafb/models"), None)

        assert response["statusCode"] == 200

    def test_unknown_route_returns_404(self):
        response = ncaafb_predict_read.lambda_handler(_api_event("/ncaafb/unknown"), None)
        assert response["statusCode"] == 404

    def test_season_is_not_served_here(self):
        # No National Ranking serving story yet -- see handler.py's own
        # docstring. Should never be routed here, but if it somehow is,
        # it must 404, not silently resurrect an unbuilt route.
        response = ncaafb_predict_read.lambda_handler(_api_event("/ncaafb/season"), None)
        assert response["statusCode"] == 404

    def test_unhandled_exception_returns_500(self):
        with patch.object(ncaafb_predict_read, "_get_storage"), \
             patch.object(ncaafb_predict_read, "_get_predictions_table"), \
             patch.object(ncaafb_predict_read, "list_events", side_effect=Exception("boom")):
            response = ncaafb_predict_read.lambda_handler(_api_event("/ncaafb/events"), None)

        assert response["statusCode"] == 500


class TestCorsHeaders:
    def test_response_includes_cors_headers(self):
        response = ncaafb_predict_read.lambda_handler(_api_event("/ncaafb/unknown"), None)
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"
