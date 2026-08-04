"""
Unit tests for the NFL game score training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_score_model.py's own orchestration (label derivation per
SCORE_TARGET, naive baseline computation, what gets handed to
run_backtest), not the tournament itself (see Source/tests/library/ml/
test_backtest.py) or any real algorithm fitting (see Source/tests/
library/ml/test_model_types.py).
"""
import io
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_score_model


def _make_df(n=10, home_scores=None, away_scores=None):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "event_date": [f"2025-09-{i + 1:02d}" for i in range(n)],
        "home_entity_id": ["KC"] * n,
        "away_entity_id": ["LAC"] * n,
        "home_elo": [1500.0 + i for i in range(n)],
        "elo_diff": [10.0] * n,
        "home_avg_points_scored": [24.0] * n,
        "home_avg_points_allowed": [20.0] * n,
        "away_avg_points_scored": [21.0] * n,
        "away_avg_points_allowed": [23.0] * n,
        "label_home_won": [i % 2 == 0 for i in range(n)],
        "label_home_score": home_scores if home_scores is not None else [24] * n,
        "label_away_score": away_scores if away_scores is not None else [17] * n,
    })


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False)
    return buffer.getvalue()


def _fake_result(model_name="score-margin", version=1):
    return {
        "winner": {"model_name": model_name, "algorithm": "xgboost", "version": version, "rmse": 5.0},
        "promoted": True,
        "candidates": [{"algorithm": "xgboost", "rmse": 5.0}],
    }


class TestModelName:
    def test_maps_each_score_target_to_its_own_model_name(self):
        assert train_score_model._model_name("margin") == "score-margin"
        assert train_score_model._model_name("home_score") == "home-score"
        assert train_score_model._model_name("away_score") == "away-score"


class TestAddLabel:
    def test_margin_is_home_minus_away(self):
        df = _make_df(3, home_scores=[24, 10, 30], away_scores=[17, 20, 30])

        result = train_score_model._add_label(df, "margin")

        assert list(result[train_score_model.LABEL_COLUMN]) == [7, -10, 0]

    def test_home_score_is_the_raw_home_score(self):
        df = _make_df(3, home_scores=[24, 10, 30], away_scores=[17, 20, 30])

        result = train_score_model._add_label(df, "home_score")

        assert list(result[train_score_model.LABEL_COLUMN]) == [24, 10, 30]

    def test_away_score_is_the_raw_away_score(self):
        df = _make_df(3, home_scores=[24, 10, 30], away_scores=[17, 20, 30])

        result = train_score_model._add_label(df, "away_score")

        assert list(result[train_score_model.LABEL_COLUMN]) == [17, 20, 30]

    def test_unknown_target_raises(self):
        df = _make_df(3)

        with pytest.raises(ValueError):
            train_score_model._add_label(df, "total_points")


class TestNaivePrediction:
    def test_margin_combines_each_teams_own_point_differential(self):
        # home diff = 24-20 = 4, away diff = 21-23 = -2 -> margin = 6
        df = _make_df(3)

        naive = train_score_model._naive_prediction(df, "margin")

        assert list(naive) == [6.0, 6.0, 6.0]

    def test_home_score_averages_home_scoring_with_away_allowing(self):
        # (24 + 23) / 2 = 23.5
        df = _make_df(3)

        naive = train_score_model._naive_prediction(df, "home_score")

        assert list(naive) == [23.5, 23.5, 23.5]

    def test_away_score_averages_away_scoring_with_home_allowing(self):
        # (21 + 20) / 2 = 20.5
        df = _make_df(3)

        naive = train_score_model._naive_prediction(df, "away_score")

        assert list(naive) == [20.5, 20.5, 20.5]

    def test_missing_history_falls_back_to_the_columns_own_mean_not_zero(self):
        df = _make_df(4)
        df.loc[3, "home_avg_points_scored"] = None  # mean of the other three (24) fills this one

        naive = train_score_model._naive_prediction(df, "home_score")

        assert naive.iloc[3] == pytest.approx((24.0 + 23.0) / 2)


class TestFeatureColumns:
    def test_excludes_identifiers_and_every_label_column(self):
        df = _make_df()

        columns = train_score_model._feature_columns(df)

        assert columns == [
            "home_elo", "elo_diff",
            "home_avg_points_scored", "home_avg_points_allowed",
            "away_avg_points_scored", "away_avg_points_allowed",
        ]


class TestTrain:
    def test_calls_run_backtest_with_regression_task_and_every_candidate(self):
        df = _make_df(10)

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_score_model.train(MagicMock(), df, "margin")

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert call.kwargs["candidates"] == train_score_model.CANDIDATES
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostRegressorAdapter", "ElasticNetAdapter", "RandomForestRegressorAdapter", "MLPRegressorAdapter",
        }
        assert result == _fake_result()

    def test_uses_the_targets_own_model_name(self):
        df = _make_df(10)

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_score_model.train(MagicMock(), df, "home_score")

        assert mock_run.call_args.args[2] == "home-score"

    def test_includes_score_target_and_row_counts_in_extra_metadata(self):
        df = _make_df(10)

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_score_model.train(MagicMock(), df, "margin")

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["score_target"] == "margin"
        assert extra["train_rows"] == 8
        assert extra["test_rows"] == 2

    def test_includes_naive_baseline_metrics_for_every_target(self):
        df = _make_df(10)

        for target in ("margin", "home_score", "away_score"):
            with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
                train_score_model.train(MagicMock(), df, target)

            naive = mock_run.call_args.kwargs["naive_baseline_metrics"]
            assert isinstance(naive["naive_baseline_rmse"], float)
            assert isinstance(naive["naive_baseline_mae"], float)

    def test_promotion_metric_is_rmse(self):
        df = _make_df(10)

        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_score_model.train(MagicMock(), df, "margin")

        assert mock_run.call_args.kwargs["promotion_metric"] == "rmse"


class TestMain:
    def test_requires_score_target_env_var(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.delenv("SCORE_TARGET", raising=False)

        with pytest.raises(KeyError):
            train_score_model.main()

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)
        monkeypatch.setenv("SCORE_TARGET", "margin")

        with pytest.raises(KeyError):
            train_score_model.main()

    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("SCORE_TARGET", "home_score")
        df = _make_df(10)
        mock_s3 = MagicMock()

        with patch.object(train_score_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_score_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_score_model, "train", return_value=_fake_result("home-score")) as mock_train:
            train_score_model.main()

        mock_load.assert_called_once_with(mock_s3, train_score_model.EVENT_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df, "home_score")
