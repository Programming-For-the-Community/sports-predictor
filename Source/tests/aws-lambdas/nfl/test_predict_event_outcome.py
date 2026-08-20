"""
Unit tests for event_prediction.predict_event's core event-outcome
quartet (win_probability/margin/home_score/away_score) and
event_prediction.reconcile_scores, the pure function that keeps those
four independently-trained predictions internally consistent.

Calls event_prediction.predict_event directly, not through nfl_predict.
lambda_handler -- predict/handler.py no longer serves this route
synchronously; GET /nfl/predictions/events/{event_id} is a read-through
cache in predict-read/handler.py that fires this same
event_prediction.predict_event asynchronously on a miss
(event_prediction.compute_and_cache_event, covered separately in
test_predict_compute_and_cache.py).

live_features and model_loader are the real modules -- storage/s3/
predictions_table are plain MagicMocks.
"""
from unittest.mock import MagicMock, patch

import pytest

import event_prediction
import live_features
import model_loader


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
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1550}) as build, \
             patch.object(model_loader, "load_current_model", side_effect=[
                 (MagicMock(), _model_card(1)),
                 (MagicMock(), _model_card(2)),
                 (MagicMock(), _model_card(3)),
                 (MagicMock(), _model_card(4)),
             ]), \
             patch.object(model_loader, "predict", side_effect=[0.62, 3.2, 24.1, 20.9]), \
             patch.object(event_prediction, "predict_event_leaders", return_value=None):
            result = event_prediction.predict_event(storage, s3, predictions_table, "401547417")

        assert result["event_key"] == "SPORT#NFL#EVENT#401547417"
        assert result["predictions"]["win_probability"] == {"home_win_probability": 0.62, "model_version": 1}
        assert result["predictions"]["margin"] == {"value": 3.2, "model_version": 2}
        assert result["predictions"]["home_score"] == {"value": 24.1, "model_version": 3}
        assert result["predictions"]["away_score"] == {"value": 20.9, "model_version": 4}
        assert build.call_args.args == (storage, "nfl", "SPORT#NFL#EVENT#401547417")
        assert build.call_args.kwargs.get("events") == storage.get_all_events.return_value

    def test_reconciles_home_and_away_score_against_the_independent_margin_prediction(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()

        # margin says +3 (home favored by a field goal), but the
        # independently-trained home_score/away_score models disagree
        # wildly with that and with each other -- 24.1 vs 3.9 implies a
        # 20+ point margin, not 3.
        with patch.object(live_features, "build_live_event_features", return_value={}), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", side_effect=[0.62, 3.0, 24.1, 3.9]), \
             patch.object(event_prediction, "predict_event_leaders", return_value=None):
            result = event_prediction.predict_event(storage, s3, predictions_table, "401547417")

        home = result["predictions"]["home_score"]["value"]
        away = result["predictions"]["away_score"]["value"]
        assert home - away == pytest.approx(3.0)  # now agrees with margin exactly
        assert home + away == pytest.approx(24.1 + 3.9)  # combined total is preserved

    def test_audits_one_predictions_table_write_per_model(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()

        with patch.object(live_features, "build_live_event_features", return_value={}), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=1.0), \
             patch.object(event_prediction, "predict_event_leaders", return_value=None):
            event_prediction.predict_event(storage, s3, predictions_table, "401547417")

        assert predictions_table.put_item.call_count == 4
