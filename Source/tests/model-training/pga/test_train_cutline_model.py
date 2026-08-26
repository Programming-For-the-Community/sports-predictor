"""
Unit tests for the PGA projected-cut-line training entrypoint --
tournament-level grain, no golfer dimension.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_cutline_model.py's own orchestration, not the tournament itself or
any real algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_cutline_model


def _make_df(n=10, cut_counts=None):
    """cut_counts: per-row cut_count value -- defaults to a real cut
    (71) for every row; a caller testing the no-cut filter overrides
    specific rows to 0."""
    if cut_counts is None:
        cut_counts = [71] * n
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "event_date": [f"2026-0{(i % 9) + 1}-01" for i in range(n)],
        "purse": [10000000.0] * n,
        "field_size": [150] * n,
        "cut_count": cut_counts,
        "label_cut_score": [float(-i) for i in range(n)],
    })


def _fake_result(version=1):
    return {
        "promotions": [{"model_name": "projected-cut-line", "algorithm": "xgboost", "version": version, "rmse": 1.5}],
        "candidates": [{"algorithm": "xgboost", "rmse": 1.5}],
    }


class TestFilterToRealCutTournaments:
    def test_drops_no_cut_tournaments(self):
        df = _make_df(10, cut_counts=[71] * 8 + [0, 0])  # last 2 had no cut

        filtered = train_cutline_model._filter_to_real_cut_tournaments(df)

        assert len(filtered) == 8


class TestFeatureColumns:
    def test_excludes_identifiers_and_cut_count(self):
        df = _make_df()

        columns = train_cutline_model._feature_columns(df)

        assert columns == ["purse", "field_size"]
        assert "cut_count" not in columns


class TestTrain:
    def test_calls_run_backtest_with_regression_task(self):
        df = _make_df(10)

        with patch.object(train_cutline_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_cutline_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["task"] == "regression"
        assert result == _fake_result()

    def test_filters_no_cut_tournaments_before_splitting(self):
        df = _make_df(10, cut_counts=[71] * 8 + [0, 0])

        with patch.object(train_cutline_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_cutline_model.train(MagicMock(), df)

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["train_rows"] + extra["test_rows"] == 8

    def test_promotion_metric_is_rmse(self):
        df = _make_df(10)

        with patch.object(train_cutline_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_cutline_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["promotion_metric"] == "rmse"


class TestMain:
    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df = _make_df(10)
        mock_s3 = MagicMock()

        with patch.object(train_cutline_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_cutline_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_cutline_model, "train", return_value=_fake_result()) as mock_train:
            train_cutline_model.main()

        mock_load.assert_called_once_with(mock_s3, train_cutline_model.CUTLINE_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df)

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_cutline_model.main()
