"""
Unit tests for the F1 DNF-probability training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_dnf_model.py's own orchestration, not the tournament itself or any
real algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_dnf_model


def _make_df(n=10, dnf_count=2):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "entity_id": [str(i) for i in range(n)],
        "constructor_entity_id": ["red_bull"] * n,
        "event_date": [f"2024-0{(i % 9) + 1}-01" for i in range(n)],
        "circuit_id": ["bahrain"] * n,
        "dnf_rate": [0.1 * i for i in range(n)],
        "label_dnf": [1 if i < dnf_count else 0 for i in range(n)],
    })


def _fake_result(version=1, algorithm="xgboost"):
    return {
        "promotions": [{"model_name": "dnf-probability", "algorithm": algorithm, "version": version, "log_loss": 0.4}],
        "candidates": [{"algorithm": algorithm, "log_loss": 0.4}],
    }


class TestFeatureColumns:
    def test_excludes_identifiers_raw_strings_and_the_label_column(self):
        df = _make_df()

        columns = train_dnf_model._feature_columns(df)

        assert columns == ["dnf_rate"]


class TestTrain:
    def test_calls_run_backtest_with_five_candidates_including_lightgbm(self):
        df = _make_df(10)

        with patch.object(train_dnf_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_dnf_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "classification"
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostClassifierAdapter", "LogisticRegressionAdapter",
            "RandomForestClassifierAdapter", "MLPClassifierAdapter", "LightGBMClassifierAdapter",
        }
        assert result == _fake_result()

    def test_splits_chronologically(self):
        df = _make_df(10)

        with patch.object(train_dnf_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_dnf_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert len(call.kwargs["split"].X_train) == 8
        assert len(call.kwargs["split"].X_test) == 2
        assert call.kwargs["split"].y_train.name == "label_dnf"


class TestMain:
    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_dnf_model.main()

    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df, mock_s3 = MagicMock(), MagicMock()

        with patch.object(train_dnf_model, "S3Manager", return_value=mock_s3),              patch.object(train_dnf_model.training_common, "load_features", return_value=df) as mock_load,              patch.object(train_dnf_model, "train") as mock_train:
            train_dnf_model.main()

        mock_load.assert_called_once_with(mock_s3, train_dnf_model.DRIVER_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df)
