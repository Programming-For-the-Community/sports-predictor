"""
Unit tests for the NFL inference Lambda's single-player-prop route (GET
/nfl/predictions/events/{event_id}/players/{entity_id}?stat=...) -- the
manually-queried counterpart to the leader-candidate scoring covered in
test_predict_leaders.py/test_predict_receiving_props.py/
test_predict_rushing_props.py. Split out of what used to be one large
test_predict.py -- see that history note in test_predict_event_outcome.py.

The nfl_predict module is registered in sys.modules by conftest.py, whose
reset_nfl_predict_singletons fixture (autouse) resets nfl_predict._storage/
_model_bucket/_predictions_table before and after every test here.
"""
import json
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


def _model_card(version: int) -> dict:
    return {"version": version, "feature_columns": []}


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
