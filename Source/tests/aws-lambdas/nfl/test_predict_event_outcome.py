"""
Unit tests for the NFL inference Lambda's core event-outcome route (GET
/nfl/predictions/events/{event_id}'s win_probability/margin/home_score/
away_score quartet) and event_prediction.reconcile_scores, the pure
function that keeps those four independently-trained predictions
internally consistent. Split out of what used to be one large
test_predict.py -- see test_predict_player_props.py, test_predict_leaders.py,
test_predict_receiving_props.py, test_predict_rushing_props.py,
test_predict_routing.py, and test_predict_season_simulation.py for this
file's siblings, one per concern.

live_features and model_loader are the real modules -- FeatureStorage,
S3Manager, and the predictions DynamoDBTable are mocked here, since this
file's only job is verifying lambda_handler's own routing/response
shaping for this one route, driven through lambda_handler exactly as API
Gateway would invoke it rather than as a separate unit test against
event_prediction.py directly.

The nfl_predict module is registered in sys.modules by conftest.py, whose
reset_nfl_predict_singletons fixture (autouse) resets nfl_predict._storage/
_model_bucket/_predictions_table before and after every test here.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import event_prediction
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


class TestReconcileScores:
    def test_already_consistent_values_are_unchanged(self):
        result = event_prediction.reconcile_scores(margin=3.2, home_score=24.1, away_score=20.9)
        assert result == {"margin": 3.2, "home_score": pytest.approx(24.1), "away_score": pytest.approx(20.9)}

    def test_splits_the_discrepancy_evenly_and_preserves_the_total(self):
        result = event_prediction.reconcile_scores(margin=3.0, home_score=24.1, away_score=3.9)
        assert result["home_score"] - result["away_score"] == pytest.approx(3.0)
        assert result["home_score"] + result["away_score"] == pytest.approx(24.1 + 3.9)

    def test_never_produces_a_negative_score(self):
        result = event_prediction.reconcile_scores(margin=-40.0, home_score=10.0, away_score=12.0)
        assert result["home_score"] >= 0
        assert result["away_score"] >= 0


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

    def test_reconciles_home_and_away_score_against_the_independent_margin_prediction(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()

        # margin says +3 (home favored by a field goal), but the
        # independently-trained home_score/away_score models disagree
        # wildly with that and with each other -- 24.1 vs 3.9 implies a
        # 20+ point margin, not 3.
        with patch.object(live_features, "build_live_event_features", return_value={}), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", side_effect=[0.62, 3.0, 24.1, 3.9]):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        body = json.loads(response["body"])
        home = body["predictions"]["home_score"]["value"]
        away = body["predictions"]["away_score"]["value"]
        assert home - away == pytest.approx(3.0)  # now agrees with margin exactly
        assert home + away == pytest.approx(24.1 + 3.9)  # combined total is preserved

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
