"""
Unit tests for the NCAAFB score training entrypoint.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_score_model


def _make_df(n=10):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "event_date": [f"2025-09-{i + 1:02d}" for i in range(n)],
        "home_entity_id": ["333"] * n,
        "away_entity_id": ["61"] * n,
        "home_avg_points_scored": [30.0] * n,
        "home_avg_points_allowed": [17.0] * n,
        "away_avg_points_scored": [24.0] * n,
        "away_avg_points_allowed": [21.0] * n,
        "label_home_score": [27 + i for i in range(n)],
        "label_away_score": [20] * n,
    })


def _fake_result(model_name="score-margin", version=1):
    return {
        "winner": {"model_name": model_name, "algorithm": "xgboost", "version": version, "rmse": 5.0},
        "promoted": True,
        "candidates": [{"algorithm": "xgboost", "rmse": 5.0}],
    }


class TestModelName:
    def test_maps_each_score_target(self):
        assert train_score_model._model_name("margin") == "score-margin"
        assert train_score_model._model_name("home_score") == "home-score"
        assert train_score_model._model_name("away_score") == "away-score"


class TestAddLabel:
    def test_margin_is_home_minus_away(self):
        df = train_score_model._add_label(_make_df(3), "margin")

        assert list(df[train_score_model.LABEL_COLUMN]) == [7, 8, 9]

    def test_unknown_target_raises(self):
        with pytest.raises(ValueError):
            train_score_model._add_label(_make_df(3), "total_points")


class TestTrain:
    def test_calls_run_backtest_with_regression_task(self):
        df = _make_df(10)

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_score_model.train(MagicMock(), df, "margin")

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostRegressorAdapter", "ElasticNetAdapter", "RandomForestRegressorAdapter", "MLPRegressorAdapter",
        }

    def test_uses_the_targets_own_model_name(self):
        df = _make_df(10)

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_score_model.train(MagicMock(), df, "home_score")

        assert mock_run.call_args.args[2] == "home-score"

    def test_promotion_metric_is_rmse(self):
        df = _make_df(10)

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_score_model.train(MagicMock(), df, "margin")

        assert mock_run.call_args.kwargs["promotion_metric"] == "rmse"


class TestMain:
    def test_requires_score_target_env_var(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.delenv("SCORE_TARGET", raising=False)

        with pytest.raises(KeyError):
            train_score_model.main()

    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("SCORE_TARGET", "margin")
        df = _make_df(10)
        mock_s3 = MagicMock()

        with patch.object(train_score_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_score_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_score_model, "train", return_value=_fake_result()) as mock_train:
            train_score_model.main()

        mock_load.assert_called_once_with(mock_s3, train_score_model.EVENT_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df, "margin")
