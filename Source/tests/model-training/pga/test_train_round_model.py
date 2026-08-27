"""
Unit tests for the PGA per-round projected-score training entrypoint --
ROUND_NUMBER-parameterized, one script for all four rounds.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_round_model.py's own orchestration, not the tournament itself or
any real algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_round_model


def _make_df(n=10, round_numbers=None, scored=None):
    """scored: which row indices have a real label_round_score_to_par --
    the rest simulate a dirty stored round result (build_round_event_
    features' _as_number() coercion), excluded from training."""
    if round_numbers is None:
        round_numbers = [1] * n
    if scored is None:
        scored = list(range(n))
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "entity_id": [str(i) for i in range(n)],
        "event_date": [f"2026-0{(i % 9) + 1}-01" for i in range(n)],
        "round_number": round_numbers,
        "purse": [10000000.0] * n,
        "overall_avg_score_to_par": [-1.0 * i for i in range(n)],
        "label_round_score_to_par": [float(-i) if i in scored else None for i in range(n)],
    })


def _fake_result(version=1):
    return {
        "promotions": [{"model_name": "round-1", "algorithm": "xgboost", "version": version, "rmse": 2.5}],
        "candidates": [{"algorithm": "xgboost", "rmse": 2.5}],
    }


class TestResolveRoundNumber:
    def test_valid_round_numbers(self, monkeypatch):
        for value in ("1", "2", "3", "4"):
            monkeypatch.setenv("ROUND_NUMBER", value)
            assert train_round_model._resolve_round_number() == int(value)

    def test_invalid_round_number_raises(self, monkeypatch):
        monkeypatch.setenv("ROUND_NUMBER", "5")
        with pytest.raises(ValueError, match="ROUND_NUMBER"):
            train_round_model._resolve_round_number()

    def test_non_numeric_round_number_raises(self, monkeypatch):
        monkeypatch.setenv("ROUND_NUMBER", "final")
        with pytest.raises(ValueError, match="ROUND_NUMBER"):
            train_round_model._resolve_round_number()

    def test_missing_round_number_raises_key_error(self, monkeypatch):
        monkeypatch.delenv("ROUND_NUMBER", raising=False)
        with pytest.raises(KeyError):
            train_round_model._resolve_round_number()


class TestFilterToRound:
    def test_keeps_only_the_requested_round(self):
        df = _make_df(10, round_numbers=[1, 1, 1, 1, 1, 2, 2, 2, 2, 2])

        filtered = train_round_model._filter_to_round(df, 2)

        assert len(filtered) == 5
        assert set(filtered["round_number"]) == {2}


class TestFilterToScoredRows:
    def test_drops_rows_with_no_label(self):
        df = _make_df(10, scored=[0, 1, 2, 3, 4, 5, 6, 7])  # last 2 have a dirty/null score

        filtered = train_round_model._filter_to_scored_rows(df)

        assert len(filtered) == 8


class TestFeatureColumns:
    def test_excludes_identifiers_and_round_number(self):
        df = _make_df()

        columns = train_round_model._feature_columns(df)

        assert columns == ["purse", "overall_avg_score_to_par"]
        assert "round_number" not in columns


class TestTrain:
    def test_drops_unscored_rows_before_splitting(self):
        df = _make_df(10, round_numbers=[1] * 10, scored=[0, 1, 2, 3, 4, 5, 6, 7])

        with patch.object(train_round_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_round_model.train(MagicMock(), df, 1)

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["train_rows"] + extra["test_rows"] == 8

    def test_calls_run_backtest_with_regression_task_and_round_scoped_model_name(self):
        df = _make_df(10, round_numbers=[3] * 10)

        with patch.object(train_round_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_round_model.train(MagicMock(), df, 3)

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert call.args[2] == "round-3"  # sport, s3, model_name positional in run_backtest's own signature
        assert result == _fake_result()

    def test_filters_to_the_requested_round_before_splitting(self):
        df = _make_df(10, round_numbers=[1] * 5 + [2] * 5)

        with patch.object(train_round_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_round_model.train(MagicMock(), df, 1)

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["train_rows"] + extra["test_rows"] == 5
        assert extra["round_number"] == 1

    def test_promotion_metric_is_rmse(self):
        df = _make_df(10)

        with patch.object(train_round_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_round_model.train(MagicMock(), df, 1)

        assert mock_run.call_args.kwargs["promotion_metric"] == "rmse"


class TestMain:
    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("ROUND_NUMBER", "2")
        df = _make_df(10, round_numbers=[2] * 10)
        mock_s3 = MagicMock()

        with patch.object(train_round_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_round_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_round_model, "train", return_value=_fake_result()) as mock_train:
            train_round_model.main()

        mock_load.assert_called_once_with(mock_s3, train_round_model.ROUND_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df, 2)

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.setenv("ROUND_NUMBER", "1")
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_round_model.main()
