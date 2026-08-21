"""
Unit tests for the NCAA MBB National Ranking training entrypoint
(team-poll granularity). library.ml.backtest.run_backtest is mocked here.

Unlike NCAAFB's own test_train_ranking_model.py, there's no "week"/
"conference"/"season_type" column here -- see train_ranking_model.py's
own NON_FEATURE_COLUMNS comment for why (poll-centric, not event/week-
centric).
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_ranking_model


def _make_df(n=10, ranked=None):
    """ranked: which row indices have a real label_current_rank -- the
    rest are unranked team-polls (None label), excluded from training."""
    if ranked is None:
        ranked = list(range(n))
    return pd.DataFrame({
        "team_id": ["150"] * n,
        "as_of_date": [f"2026-01-{i + 1:02d}" for i in range(n)],
        "season": [2026] * n,
        "elo": [1600.0 + i for i in range(n)],
        "wins": [i for i in range(n)],
        "label_current_rank": [float(i + 1) if i in ranked else None for i in range(n)],
    })


def _fake_result(version=1):
    return {
        "promotions": [{"model_name": "national-ranking", "algorithm": "xgboost", "version": version, "rmse": 3.0}],
        "candidates": [{"algorithm": "xgboost", "rmse": 3.0}],
    }


class TestFilterToRankedWeeks:
    def test_drops_rows_with_no_label(self):
        df = _make_df(10, ranked=[0, 1, 2, 3, 4, 5, 6, 7])  # last 2 unranked

        filtered = train_ranking_model._filter_to_ranked_weeks(df)

        assert len(filtered) == 8


class TestFeatureColumns:
    def test_excludes_identifiers_and_non_numeric_columns(self):
        df = _make_df(10)

        columns = train_ranking_model._feature_columns(df)

        assert columns == ["elo", "wins"]

    def test_label_column_never_leaks_into_feature_columns(self):
        df = _make_df(10)

        columns = train_ranking_model._feature_columns(df)

        assert train_ranking_model.LABEL_COLUMN not in columns


class TestTrain:
    def test_calls_run_backtest_with_regression_task(self):
        df = _make_df(10)

        with patch.object(train_ranking_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_ranking_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostRegressorAdapter", "ElasticNetAdapter", "RandomForestRegressorAdapter", "MLPRegressorAdapter",
        }
        assert result == _fake_result()

    def test_uses_the_national_ranking_model_name(self):
        df = _make_df(10)

        with patch.object(train_ranking_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_ranking_model.train(MagicMock(), df)

        assert mock_run.call_args.args[2] == "national-ranking"

    def test_filters_unranked_weeks_before_splitting(self):
        df = _make_df(10, ranked=[0, 1, 2, 3, 4, 5, 6])  # 7 ranked rows

        with patch.object(train_ranking_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_ranking_model.train(MagicMock(), df)

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["train_rows"] + extra["test_rows"] == 7

    def test_naive_baseline_predicts_the_training_medians_rank(self):
        df = _make_df(10)  # ranks 1..10, chronological split -> train on 1..8

        with patch.object(train_ranking_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_ranking_model.train(MagicMock(), df)

        naive = mock_run.call_args.kwargs["naive_baseline_metrics"]
        assert "naive_baseline_rmse" in naive
        assert "naive_baseline_mae" in naive

    def test_promotion_metric_is_rmse(self):
        df = _make_df(10)

        with patch.object(train_ranking_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_ranking_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["promotion_metric"] == "rmse"


class TestMain:
    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df = _make_df(10)
        mock_s3 = MagicMock()

        with patch.object(train_ranking_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_ranking_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_ranking_model, "train", return_value=_fake_result()) as mock_train:
            train_ranking_model.main()

        mock_load.assert_called_once_with(mock_s3, train_ranking_model.RANKING_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df)

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_ranking_model.main()
