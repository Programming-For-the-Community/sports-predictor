"""
Unit tests for the F1 projected-qualifying-position training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_qualifying_model.py's own orchestration (including the filter-to-
scored-rows step), not the tournament itself or any real algorithm
fitting.
"""
from unittest.mock import MagicMock, patch

import pandas as pd

import train_qualifying_model


def _make_df(n=10, no_qualifying_count=0):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "entity_id": [str(i) for i in range(n)],
        "constructor_entity_id": ["red_bull"] * n,
        "event_date": [f"2024-0{(i % 9) + 1}-01" for i in range(n)],
        "circuit_id": ["bahrain"] * n,
        "qualifying_avg_gap_to_pole_seconds": [0.1 * i for i in range(n)],
        "label_qualifying_position": [None if i < no_qualifying_count else float(i + 1) for i in range(n)],
    })


def _fake_result(version=1, algorithm="xgboost"):
    return {
        "promotions": [{"model_name": "projected-qualifying-position", "algorithm": algorithm, "version": version, "rmse": 2.1}],
        "candidates": [{"algorithm": algorithm, "rmse": 2.1}],
    }


class TestFeatureColumns:
    def test_excludes_identifiers_raw_strings_and_the_label_column(self):
        df = _make_df()

        columns = train_qualifying_model._feature_columns(df)

        assert columns == ["qualifying_avg_gap_to_pole_seconds"]


class TestFilterToScoredRows:
    def test_drops_rows_with_no_merged_qualifying_data(self):
        df = _make_df(10, no_qualifying_count=3)

        filtered = train_qualifying_model._filter_to_scored_rows(df)

        assert len(filtered) == 7
        assert filtered["label_qualifying_position"].notna().all()


class TestTrain:
    def test_filters_before_splitting_and_calls_run_backtest_with_five_regressors(self):
        df = _make_df(10, no_qualifying_count=2)

        with patch.object(train_qualifying_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_qualifying_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostRegressorAdapter", "ElasticNetAdapter",
            "RandomForestRegressorAdapter", "MLPRegressorAdapter", "LightGBMRegressorAdapter",
        }
        assert len(call.kwargs["X_train"]) + len(call.kwargs["X_test"]) == 8
        assert result == _fake_result()

    def test_naive_baseline_metrics_are_computed_against_the_median(self):
        df = _make_df(10)

        with patch.object(train_qualifying_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_qualifying_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert "naive_baseline_rmse" in call.kwargs["naive_baseline_metrics"]
        assert "naive_baseline_mae" in call.kwargs["naive_baseline_metrics"]
