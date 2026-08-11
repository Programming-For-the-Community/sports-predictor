"""
Unit tests for event_prediction.predict_player_prop -- the manually-
queried counterpart to the leader-candidate scoring covered in
test_predict_leaders.py/test_predict_receiving_props.py/
test_predict_rushing_props.py. Split out of what used to be one large
test_predict.py -- see that history note in test_predict_event_outcome.py.

Calls event_prediction.predict_player_prop directly, not through
nfl_predict.lambda_handler -- see test_predict_event_outcome.py's own
docstring for why (the route moved to predict-read/handler.py's cache-
backed routing; the missing-stat-query-param 400 that used to live here
is now covered there, test_predict_read.py, since that's the Lambda that
actually validates query params now).
"""
from unittest.mock import MagicMock, patch

import event_prediction
import live_features
import model_loader


def _model_card(version: int) -> dict:
    return {"version": version, "feature_columns": []}


class TestPlayerPropRoute:
    def test_returns_the_requested_stats_prediction(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()

        with patch.object(live_features, "build_live_player_features", return_value={"avg_passing_yards": 275}) as build, \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(5))) as load, \
             patch.object(model_loader, "predict", return_value=267.4):
            result = event_prediction.predict_player_prop(storage, s3, predictions_table, "401547417", "mahomes-patrick", "passing_yards")

        assert result["entity_key"] == "SPORT#NFL#ENTITY#mahomes-patrick"
        assert result["stat"] == "passing_yards"
        assert result["prediction"] == {"value": 267.4, "model_version": 5}
        build.assert_called_once_with(storage, "nfl", "SPORT#NFL#EVENT#401547417", "mahomes-patrick")
        load.assert_called_once_with(s3, "nfl", "player-prop-passing-yards")
        predictions_table.put_item.assert_called_once()
