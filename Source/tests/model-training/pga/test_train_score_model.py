"""
Unit tests for the PGA projected-score-to-par training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_score_model.py's own orchestration, not the tournament itself or
any real algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_score_model


def _make_df(n=10, scored=None):
    """scored: which row indices have a real label_score_to_par -- the
    rest simulate a withdrawal with no recorded score at all, excluded
    from training."""
    if scored is None:
        scored = list(range(n))
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "entity_id": [str(i) for i in range(n)],
        "event_date": [f"2026-0{(i % 9) + 1}-01" for i in range(n)],
        "avg_finish_position": [10.0 + i for i in range(n)],
        "purse": [10000000.0] * n,
        "label_score_to_par": [float(-i) if i in scored else None for i in range(n)],
    })


def _fake_result(version=1):
    return {
        "promotions": [{"model_name": "projected-score-to-par", "algorithm": "xgboost", "version": version, "rmse": 3.5}],
        "candidates": [{"algorithm": "xgboost", "rmse": 3.5}],
    }


class TestFilterToScoredRows:
    def test_drops_rows_with_no_label(self):
        df = _make_df(10, scored=[0, 1, 2, 3, 4, 5, 6, 7])  # last 2 have no score

        filtered = train_score_model._filter_to_scored_rows(df)

        assert len(filtered) == 8


class TestFeatureColumns:
    def test_excludes_identifiers_and_the_label_column(self):
        df = _make_df()

        columns = train_score_model._feature_columns(df)

        assert columns == ["avg_finish_position", "purse"]


class TestTrain:
    def test_calls_run_backtest_with_regression_task_and_no_lightgbm(self):
        df = _make_df(10)

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_score_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostRegressorAdapter", "ElasticNetAdapter", "RandomForestRegressorAdapter", "MLPRegressorAdapter",
        }
        assert result == _fake_result()

    def test_filters_unscored_rows_before_splitting(self):
        df = _make_df(10, scored=[0, 1, 2, 3, 4, 5, 6, 7])

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_score_model.train(MagicMock(), df)

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["train_rows"] + extra["test_rows"] == 8

    def test_promotion_metric_is_rmse(self):
        df = _make_df(10)

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_score_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["promotion_metric"] == "rmse"

    def test_naive_baseline_is_the_median_score(self):
        df = _make_df(10)  # label_score_to_par = [0, -1, -2, ..., -9]

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_score_model.train(MagicMock(), df)

        assert "naive_baseline_rmse" in mock_run.call_args.kwargs["naive_baseline_metrics"]
        assert "naive_baseline_mae" in mock_run.call_args.kwargs["naive_baseline_metrics"]


class TestMain:
    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df = _make_df(10)
        mock_s3 = MagicMock()

        with patch.object(train_score_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_score_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_score_model, "train", return_value=_fake_result()) as mock_train:
            train_score_model.main()

        mock_load.assert_called_once_with(mock_s3, train_score_model.GOLFER_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df)

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_score_model.main()
