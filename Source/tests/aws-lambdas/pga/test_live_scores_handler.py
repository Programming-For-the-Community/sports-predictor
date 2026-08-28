"""
Unit tests for the live-scores Lambda's own lambda_handler -- dispatch
between its two trigger shapes (EventBridge's LiveScoreRefresh detail-type
vs. API Gateway's GET /pga/live-scores) and response shaping/error
handling. live_scores.py's own logic is covered directly in
test_live_scores.py -- this file only verifies routing.

The pga_live_scores module is registered in sys.modules by conftest.py.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import live_scores
import pga_live_scores


@pytest.fixture(autouse=True)
def reset_singleton():
    pga_live_scores._storage = None
    yield
    pga_live_scores._storage = None


def _api_event(resource: str) -> dict:
    return {"resource": resource}


class TestDispatch:
    def test_refresh_trigger_calls_live_scores_refresh_not_the_api_route(self):
        pga_live_scores._storage = MagicMock()
        with patch.object(live_scores, "refresh", return_value={"polled": 1}) as mock_refresh:
            result = pga_live_scores.lambda_handler({"detail-type": "LiveScoreRefresh"}, None)

        assert result == {"polled": 1}
        mock_refresh.assert_called_once()

    def test_live_scores_route_returns_cached_events(self):
        pga_live_scores._storage = MagicMock()
        with patch.object(live_scores, "get_live_scores", return_value={"events": {"1": {"status": "scheduled"}}}):
            response = pga_live_scores.lambda_handler(_api_event("/pga/live-scores"), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"events": {"1": {"status": "scheduled"}}}

    def test_unknown_resource_returns_404(self):
        response = pga_live_scores.lambda_handler(_api_event("/pga/nonsense"), None)

        assert response["statusCode"] == 404

    def test_an_unhandled_exception_returns_500_not_a_raw_traceback(self):
        pga_live_scores._storage = MagicMock()
        with patch.object(live_scores, "get_live_scores", side_effect=RuntimeError("boom")):
            response = pga_live_scores.lambda_handler(_api_event("/pga/live-scores"), None)

        assert response["statusCode"] == 500
