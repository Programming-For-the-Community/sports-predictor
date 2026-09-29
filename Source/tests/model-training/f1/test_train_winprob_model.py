"""
Unit tests for the F1 win-probability training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_winprob_model.py's own orchestration, not the tournament itself or
any real algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_winprob_model


def _make_df(n=10, winner_count=1):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "entity_id": [str(i) for i in range(n)],
        "constructor_entity_id": ["red_bull"] * n,
        "event_date": [f"2024-0{(i % 9) + 1}-01" for i in range(n)],
        "circuit_id": ["bahrain"] * n,
        "avg_finish_position": [10.0 + i for i in range(n)],
        "grid_position": [i + 1 for i in range(n)],
        "label_win": [1 if i < winner_count else 0 for i in range(n)],
    })


def _fake_result(version=1, algorithm="xgboost"):
    return {
        "promotions": [{"model_name": "win-probability", "algorithm": algorithm, "version": version, "log_loss": 0.4}],
        "candidates": [{"algorithm": algorithm, "log_loss": 0.4}],
    }


class TestFeatureColumns:
    def test_excludes_identifiers_raw_strings_and_the_label_column(self):
        df = _make_df()

        columns = train_winprob_model._feature_columns(df)

        assert columns == ["avg_finish_position", "grid_position"]
        assert "constructor_entity_id" not in columns
        assert "circuit_id" not in columns


class TestTrain:
    def test_calls_run_backtest_with_five_candidates_including_lightgbm(self):
        df = _make_df(10)

        with patch.object(train_winprob_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_winprob_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "classification"
        assert call.kwargs["candidates"] == train_winprob_model.CANDIDATES
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostClassifierAdapter", "LogisticRegressionAdapter",
            "RandomForestClassifierAdapter", "MLPClassifierAdapter", "LightGBMClassifierAdapter",
        }
        assert result == _fake_result()

    def test_splits_chronologically_and_builds_numeric_frames_of_the_right_columns(self):
        df = _make_df(10)

        with patch.object(train_winprob_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_winprob_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert list(call.kwargs["split"].X_train.columns) == ["avg_finish_position", "grid_position"]
        assert len(call.kwargs["split"].X_train) == 8
        assert len(call.kwargs["split"].X_test) == 2
        assert call.kwargs["split"].y_train.name == "label_win"

    def test_naive_baseline_uses_the_majority_class_in_the_holdout(self):
        df = _make_df(10, winner_count=1)  # heavily majority-negative

        with patch.object(train_winprob_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_winprob_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["naive_baseline_metrics"]["naive_baseline_accuracy"] >= 0.5


class TestMain:
    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_winprob_model.main()

    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df, mock_s3 = MagicMock(), MagicMock()

        with patch.object(train_winprob_model, "S3Manager", return_value=mock_s3),              patch.object(train_winprob_model.training_common, "load_features", return_value=df) as mock_load,              patch.object(train_winprob_model, "train") as mock_train:
            train_winprob_model.main()

        mock_load.assert_called_once_with(mock_s3, train_winprob_model.DRIVER_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df)
