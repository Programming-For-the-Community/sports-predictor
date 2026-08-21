"""
Unit tests for the NCAA MBB win-probability training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_win_probability_model.py's own orchestration, not the tournament
itself or any real algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import train_win_probability_model


def _make_df(n=10):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "event_date": [f"2025-12-{i + 1:02d}" for i in range(n)],
        "home_entity_id": ["150"] * n,
        "away_entity_id": ["153"] * n,
        "home_elo": [1500.0 + i for i in range(n)],
        "elo_diff": [10.0] * n,
        "label_home_won": [i % 2 == 0 for i in range(n)],
        "label_home_score": [80] * n,
        "label_away_score": [74] * n,
    })


def _fake_result(version=1, algorithm="xgboost"):
    return {
        "promotions": [{"model_name": "win-probability", "algorithm": algorithm, "version": version, "log_loss": 0.6}],
        "candidates": [{"algorithm": algorithm, "log_loss": 0.6}],
    }


class TestFeatureColumns:
    def test_excludes_identifiers_and_every_label_column(self):
        df = _make_df()

        columns = train_win_probability_model._feature_columns(df)

        assert columns == ["home_elo", "elo_diff"]


class TestTrain:
    def test_calls_run_backtest_with_five_candidates_including_lightgbm(self):
        df = _make_df(10)

        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_win_probability_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "classification"
        assert call.kwargs["candidates"] == train_win_probability_model.CANDIDATES
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostClassifierAdapter", "LogisticRegressionAdapter",
            "RandomForestClassifierAdapter", "MLPClassifierAdapter", "LightGBMClassifierAdapter",
        }
        assert result == _fake_result()

    def test_splits_chronologically_and_builds_numeric_frames_of_the_right_columns(self):
        df = _make_df(10)

        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_win_probability_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert list(call.kwargs["X_train"].columns) == ["home_elo", "elo_diff"]
        assert len(call.kwargs["X_train"]) == 8
        assert len(call.kwargs["X_test"]) == 2
        assert call.kwargs["y_train"].name == "label_home_won"
        assert call.kwargs["X_test"].index.tolist() == df.index[-2:].tolist()

    def test_naive_baseline_accuracy_is_fraction_of_home_wins_in_test_set(self):
        df = _make_df(10)

        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_win_probability_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["naive_baseline_metrics"] == {"naive_baseline_accuracy": 0.5}

    def test_passes_row_counts_and_date_ranges_in_extra_metadata(self):
        df = _make_df(10)

        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_win_probability_model.train(MagicMock(), df)

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["train_rows"] == 8
        assert extra["test_rows"] == 2
        assert "train_date_range" in extra
        assert "test_date_range" in extra

    def test_promotion_metric_is_log_loss(self):
        df = _make_df(10)

        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_win_probability_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["promotion_metric"] == "log_loss"

    def test_all_null_feature_column_is_still_numeric_not_object(self):
        df = _make_df(10)
        df["some_sparse_column"] = [None] * len(df)  # entirely null -- object dtype from Parquet

        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_win_probability_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["X_train"]["some_sparse_column"].dtype == np.float64


class TestMain:
    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df = _make_df(10)
        mock_s3 = MagicMock()

        with patch.object(train_win_probability_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_win_probability_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_win_probability_model, "train", return_value=_fake_result()) as mock_train:
            train_win_probability_model.main()

        mock_load.assert_called_once_with(mock_s3, train_win_probability_model.EVENT_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df)

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_win_probability_model.main()
