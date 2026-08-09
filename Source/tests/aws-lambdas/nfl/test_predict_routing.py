"""
Unit tests for the NFL inference Lambda's request routing (resource
dispatch, CORS headers, routes moved to other Lambdas) and its generic
exception-to-status-code mapping (event_prediction/live_features errors
-> 404/422/503/500). Split out of what used to be one large
test_predict.py -- see test_predict_event_outcome.py's own history note.

The nfl_predict module is registered in sys.modules by conftest.py, whose
reset_nfl_predict_singletons fixture (autouse) resets nfl_predict._storage/
_model_bucket/_predictions_table before and after every test here.
"""
from unittest.mock import MagicMock, patch

import live_features
import model_loader
import nfl_predict


def _api_event(resource: str, path_params: dict | None = None, query_params: dict | None = None) -> dict:
    return {
        "resource": resource,
        "pathParameters": path_params or {},
        "queryStringParameters": query_params or {},
    }


class TestRouting:
    def test_unknown_resource_is_a_404(self):
        response = nfl_predict.lambda_handler(_api_event("/nfl/something-else"), None)

        assert response["statusCode"] == 404

    def test_every_response_carries_cors_headers(self):
        response = nfl_predict.lambda_handler(_api_event("/nfl/something-else"), None)

        assert response["headers"]["Access-Control-Allow-Origin"] == "*"

    def test_events_and_models_are_not_served_here(self):
        # Moved to predict-read/handler.py (library.serving.nfl_reads,
        # covered by Source/tests/library/serving/test_nfl_reads.py and
        # Source/tests/aws-lambdas/nfl/test_predict_read.py) -- neither
        # route ever loads an ML model artifact, so they're served by a
        # separate, much lighter Lambda that never imports xgboost/
        # scikit-learn/pandas. API Gateway is repointed to send this
        # traffic there instead; this Lambda should never see it, but if
        # it somehow did, it must 404, not silently resurrect the route.
        assert nfl_predict.lambda_handler(_api_event("/nfl/events"), None)["statusCode"] == 404
        assert nfl_predict.lambda_handler(_api_event("/nfl/models"), None)["statusCode"] == 404

    def test_season_is_not_served_here(self):
        # Same reasoning, moved even later -- see
        # test_predict_season_simulation.py's own TestScheduledSeasonProjection
        # and predict/handler.py's own docstring. API Gateway is repointed
        # to nfl_predict_read (api-gateway-nfl-predict.tf); a GET
        # /nfl/season request should never reach this Lambda, but if it
        # somehow did, it must 404, not resurrect the old live-compute
        # route.
        assert nfl_predict.lambda_handler(_api_event("/nfl/season"), None)["statusCode"] == 404


class TestErrorMapping:
    def test_event_not_found_is_a_404(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        with patch.object(live_features, "build_live_event_features", side_effect=live_features.EventNotFoundError("nope")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "missing"}), None,
            )

        assert response["statusCode"] == 404

    def test_malformed_event_is_a_422(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        with patch.object(live_features, "build_live_event_features", side_effect=live_features.MalformedEventError("bad")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 422

    def test_no_promoted_model_is_a_503(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        with patch.object(live_features, "build_live_event_features", return_value={}), \
             patch.object(model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError("none yet")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 503

    def test_unexpected_exception_is_a_500_not_a_raw_502(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        with patch.object(live_features, "build_live_event_features", side_effect=RuntimeError("boom")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 500
