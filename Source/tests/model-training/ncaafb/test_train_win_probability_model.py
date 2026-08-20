"""
Unit tests for the NCAAFB win-probability training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
orchestration, not the tournament itself or any real algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import train_win_probability_model


def _make_df(n=10):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "event_date": [f"2025-09-{i + 1:02d}" for i in range(n)],
        "home_entity_id": ["333"] * n,
        "away_entity_id": ["61"] * n,
        "home_elo": [1500.0 + i for i in range(n)],
        "elo_diff": [10.0] * n,
        "label_home_won": [i % 2 == 0 for i in range(n)],
        "label_home_score": [27] * n,
        "label_away_score": [20] * n,
    })


def _fake_result(version=1, algorithm="xgboost"):
    return {
        "winner": {"model_name": "win-probability", "algorithm": algorithm, "version": version, "log_loss": 0.6},
        "promoted": True,
        "candidates": [{"algorithm": algorithm, "log_loss": 0.6}],
    }


class TestFeatureColumns:
    def test_excludes_identifiers_and_every_label_column(self):
        df = _make_df()

        columns = train_win_probability_model._feature_columns(df)

        assert columns == ["home_elo", "elo_diff"]

    def test_no_venue_city_or_state_columns_exist(self):
        # build_event_features never surfaces raw venue strings, so
        # there's nothing to exclude here.
        df = _make_df()
        df["venue_indoor"] = [False] * len(df)

        columns = train_win_probability_model._feature_columns(df)

        assert "venue_indoor" in columns
        assert "venue_city" not in df.columns


class TestTrain:
    def test_calls_run_backtest_with_both_candidates_and_classification_task(self):
        df = _make_df(10)

        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_win_probability_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "classification"
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostClassifierAdapter", "LogisticRegressionAdapter",
            "RandomForestClassifierAdapter", "MLPClassifierAdapter",
        }
        assert result == _fake_result()

    def test_splits_chronologically(self):
        df = _make_df(10)

        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_win_probability_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert len(call.kwargs["X_train"]) == 8
        assert len(call.kwargs["X_test"]) == 2
        assert call.kwargs["X_test"].index.tolist() == df.index[-2:].tolist()

    def test_all_null_feature_column_is_still_numeric_not_object(self):
        df = _make_df(10)
        df["home_current_rank"] = [None] * len(df)

        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_win_probability_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["X_train"]["home_current_rank"].dtype == np.float64

    def test_promotion_metric_is_log_loss(self):
        df = _make_df(10)

        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_win_probability_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["promotion_metric"] == "log_loss"


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
