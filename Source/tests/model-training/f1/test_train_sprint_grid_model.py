"""
Unit tests for the F1 projected-Sprint-grid training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_sprint_grid_model.py's own orchestration, not the tournament
itself or any real algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import pandas as pd

import train_sprint_grid_model


def _make_df(n=10, dns_count=0):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "entity_id": [str(i) for i in range(n)],
        "constructor_entity_id": ["red_bull"] * n,
        "event_date": [f"2024-0{(i % 9) + 1}-01" for i in range(n)],
        "circuit_id": ["shanghai"] * n,
        "avg_finish_position": [10.0 + i for i in range(n)],
        "label_sprint_grid_position": [None if i < dns_count else float(i + 1) for i in range(n)],
    })


def _fake_result(version=1, algorithm="xgboost"):
    return {
        "promotions": [{"model_name": "projected-sprint-grid-position", "algorithm": algorithm, "version": version, "rmse": 3.0}],
        "candidates": [{"algorithm": algorithm, "rmse": 3.0}],
    }


class TestFeatureColumns:
    def test_excludes_identifiers_raw_strings_and_the_label_column(self):
        df = _make_df()

        columns = train_sprint_grid_model._feature_columns(df)

        assert columns == ["avg_finish_position"]


class TestFilterToScoredRows:
    def test_drops_rows_with_no_real_sprint_grid_position(self):
        df = _make_df(10, dns_count=2)

        filtered = train_sprint_grid_model._filter_to_scored_rows(df)

        assert len(filtered) == 8
        assert filtered["label_sprint_grid_position"].notna().all()


class TestTrain:
    def test_calls_run_backtest_with_five_regressors(self):
        df = _make_df(10)

        with patch.object(train_sprint_grid_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_sprint_grid_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostRegressorAdapter", "ElasticNetAdapter",
            "RandomForestRegressorAdapter", "MLPRegressorAdapter", "LightGBMRegressorAdapter",
        }
        assert result == _fake_result()

    def test_splits_chronologically(self):
        df = _make_df(10)

        with patch.object(train_sprint_grid_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_sprint_grid_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert len(call.kwargs["X_train"]) == 8
        assert len(call.kwargs["X_test"]) == 2
        assert call.kwargs["y_train"].name == "label_sprint_grid_position"
