"""
Unit tests for the PGA match win-probability training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_match_winprob_model.py's own orchestration, not the tournament
itself or any real algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import train_match_winprob_model


def _make_df(n=10, home_win_count=5, halved_count=0):
    labels = [True] * home_win_count + [False] * (n - home_win_count - halved_count) + [None] * halved_count
    return pd.DataFrame({
        "event_key": [f"E{i}-match-{i}" for i in range(n)],
        "event_date": [f"2026-0{(i % 9) + 1}-01" for i in range(n)],
        "match_format": ["singles"] * n,
        "is_singles": [True] * n,
        "home_avg_score_to_par": [-5.0 + i for i in range(n)],
        "away_avg_score_to_par": [2.0] * n,
        "label_home_won": labels,
    })


def _fake_result(version=1, algorithm="xgboost"):
    return {
        "promotions": [{"model_name": "match-win-probability", "algorithm": algorithm, "version": version, "log_loss": 0.5}],
        "candidates": [{"algorithm": algorithm, "log_loss": 0.5}],
    }


class TestFeatureColumns:
    def test_excludes_identifiers_match_format_and_every_label_column(self):
        df = _make_df()

        columns = train_match_winprob_model._feature_columns(df)

        assert columns == ["is_singles", "home_avg_score_to_par", "away_avg_score_to_par"]


class TestTrain:
    def test_calls_run_backtest_with_five_candidates_including_lightgbm(self):
        df = _make_df(10)

        with patch.object(train_match_winprob_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_match_winprob_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "classification"
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostClassifierAdapter", "LogisticRegressionAdapter",
            "RandomForestClassifierAdapter", "MLPClassifierAdapter", "LightGBMClassifierAdapter",
        }
        assert result == _fake_result()

    def test_halved_matches_are_dropped_before_the_split(self):
        df = _make_df(10, home_win_count=4, halved_count=2)  # 4 True, 4 False, 2 None

        with patch.object(train_match_winprob_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_match_winprob_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert len(call.kwargs["X_train"]) + len(call.kwargs["X_test"]) == 8

    def test_splits_chronologically_and_builds_numeric_frames_of_the_right_columns(self):
        df = _make_df(10)

        with patch.object(train_match_winprob_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_match_winprob_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert list(call.kwargs["X_train"].columns) == ["is_singles", "home_avg_score_to_par", "away_avg_score_to_par"]
        assert len(call.kwargs["X_train"]) == 8
        assert len(call.kwargs["X_test"]) == 2
        assert call.kwargs["y_train"].name == "label_home_won"

    def test_labels_are_coerced_to_int(self):
        df = _make_df(10)

        with patch.object(train_match_winprob_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_match_winprob_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["y_train"].dtype == np.int64

    def test_passes_row_counts_and_date_ranges_in_extra_metadata(self):
        df = _make_df(10)

        with patch.object(train_match_winprob_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_match_winprob_model.train(MagicMock(), df)

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["train_rows"] == 8
        assert extra["test_rows"] == 2

    def test_promotion_metric_is_log_loss(self):
        df = _make_df(10)

        with patch.object(train_match_winprob_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_match_winprob_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["promotion_metric"] == "log_loss"

    def test_all_null_feature_column_is_still_numeric_not_object(self):
        df = _make_df(10)
        df["some_sparse_column"] = [None] * len(df)

        with patch.object(train_match_winprob_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_match_winprob_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["X_train"]["some_sparse_column"].dtype == np.float64


class TestMain:
    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df = _make_df(10)
        mock_s3 = MagicMock()

        with patch.object(train_match_winprob_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_match_winprob_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_match_winprob_model, "train", return_value=_fake_result()) as mock_train:
            train_match_winprob_model.main()

        mock_load.assert_called_once_with(mock_s3, train_match_winprob_model.MATCH_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df)

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_match_winprob_model.main()
