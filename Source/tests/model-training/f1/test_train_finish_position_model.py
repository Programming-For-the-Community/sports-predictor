"""
Unit tests for the F1 projected-finish-position training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_finish_position_model.py's own orchestration (including the
filter-to-classified-rows step), not the tournament itself or any real
algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_finish_position_model


def _make_df(n=10, dnf_count=0):
    # The first dnf_count rows have no real finish position (a DNF) --
    # label_finish_position is None for those, same as
    # library/normalize/f1.py's map_status "dnf"/"dsq"/"dns" outcomes.
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "entity_id": [str(i) for i in range(n)],
        "constructor_entity_id": ["red_bull"] * n,
        "event_date": [f"2024-0{(i % 9) + 1}-01" for i in range(n)],
        "circuit_id": ["bahrain"] * n,
        "avg_finish_position": [10.0 + i for i in range(n)],
        "label_finish_position": [None if i < dnf_count else float(i + 1) for i in range(n)],
    })


def _fake_result(version=1, algorithm="xgboost"):
    return {
        "promotions": [{"model_name": "projected-finish-position", "algorithm": algorithm, "version": version, "rmse": 2.1}],
        "candidates": [{"algorithm": algorithm, "rmse": 2.1}],
    }


class TestFeatureColumns:
    def test_excludes_identifiers_raw_strings_and_the_label_column(self):
        df = _make_df()

        columns = train_finish_position_model._feature_columns(df)

        assert columns == ["avg_finish_position"]


class TestFilterToScoredRows:
    def test_drops_rows_with_no_real_finish_position(self):
        df = _make_df(10, dnf_count=3)

        filtered = train_finish_position_model._filter_to_scored_rows(df)

        assert len(filtered) == 7
        assert filtered["label_finish_position"].notna().all()


class TestTrain:
    def test_filters_before_splitting_and_calls_run_backtest_with_five_regressors(self):
        df = _make_df(10, dnf_count=2)

        with patch.object(train_finish_position_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_finish_position_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostRegressorAdapter", "ElasticNetAdapter",
            "RandomForestRegressorAdapter", "MLPRegressorAdapter", "LightGBMRegressorAdapter",
        }
        # 8 scored rows total (10 - 2 dnf), 80/20 chronological split.
        assert len(call.kwargs["split"].X_train) + len(call.kwargs["split"].X_test) == 8
        assert result == _fake_result()

    def test_naive_baseline_metrics_are_computed_against_the_median(self):
        df = _make_df(10)

        with patch.object(train_finish_position_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_finish_position_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert "naive_baseline_rmse" in call.kwargs["naive_baseline_metrics"]
        assert "naive_baseline_mae" in call.kwargs["naive_baseline_metrics"]


class TestMain:
    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_finish_position_model.main()

    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df, mock_s3 = MagicMock(), MagicMock()

        with patch.object(train_finish_position_model.training_common, "S3Manager", return_value=mock_s3),              patch.object(train_finish_position_model.training_common, "load_features", return_value=df) as mock_load,              patch.object(train_finish_position_model, "train") as mock_train:
            train_finish_position_model.main()

        mock_load.assert_called_once_with(mock_s3, train_finish_position_model.DRIVER_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df)
