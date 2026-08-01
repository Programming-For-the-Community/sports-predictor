"""
Unit tests for the NFL game score training entrypoint. xgboost's
XGBRegressor is ALWAYS mocked here, same mocking boundary as
test_train_model.py draws around XGBClassifier -- these tests verify
train_score_model.py's own orchestration (label derivation per
SCORE_TARGET, naive baseline computation, versioned S3 writes), never a
real fit.
"""
import io
from unittest.mock import MagicMock, patch

import numpy as np
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
    def _mock_model(self):
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([5.0, -3.0])
        mock_model.get_booster.return_value.get_score.return_value = {}
        return mock_model

    def test_fits_mocked_model_and_computes_metrics(self):
        df = _make_df(10)
        mock_model = self._mock_model()

        with patch.object(train_score_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_score_model.xgb, "XGBRegressor", return_value=mock_model):
            model, metadata = train_score_model.train(df, "margin")

        mock_model.fit.assert_called_once()
        assert model is mock_model
        assert metadata["score_target"] == "margin"
        assert metadata["train_rows"] == 8
        assert metadata["test_rows"] == 2

    def test_metrics_are_rmse_and_mae_not_classification_metrics(self):
        df = _make_df(10)
        mock_model = self._mock_model()

        with patch.object(train_score_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_score_model.xgb, "XGBRegressor", return_value=mock_model):
            _, metadata = train_score_model.train(df, "home_score")

        assert isinstance(metadata["rmse"], float)
        assert isinstance(metadata["mae"], float)
        assert "accuracy" not in metadata
        assert "log_loss" not in metadata

    def test_includes_naive_baseline_metrics_for_every_target(self):
        df = _make_df(10)
        mock_model = self._mock_model()

        for target in ("margin", "home_score", "away_score"):
            with patch.object(train_score_model, "_tune_hyperparameters", return_value={}), \
                 patch.object(train_score_model.xgb, "XGBRegressor", return_value=mock_model):
                _, metadata = train_score_model.train(df, target)

            assert isinstance(metadata["naive_baseline_rmse"], float)
            assert isinstance(metadata["naive_baseline_mae"], float)

    def test_feature_importances_default_unused_features_to_zero(self):
        df = _make_df(10)
        mock_model = self._mock_model()
        mock_model.get_booster.return_value.get_score.return_value = {"home_elo": 5.0}

        with patch.object(train_score_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_score_model.xgb, "XGBRegressor", return_value=mock_model):
            _, metadata = train_score_model.train(df, "margin")

        assert metadata["feature_importances"]["home_elo"] == 5.0
        assert metadata["feature_importances"]["elo_diff"] == 0.0


class TestTuneHyperparameters:
    def test_uses_time_series_split_and_rmse_scoring(self):
        df = train_score_model._add_label(_make_df(10), "margin")
        feature_columns = train_score_model._feature_columns(df)
        X, y = df[feature_columns], df[train_score_model.LABEL_COLUMN]
        mock_search = MagicMock()
        mock_search.best_params_ = {"max_depth": 3}
        mock_search.best_score_ = -7.5

        with patch.object(train_score_model, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls:
            result = train_score_model._tune_hyperparameters(X, y)

        mock_search.fit.assert_called_once()
        call_kwargs = mock_search_cls.call_args.kwargs
        assert isinstance(call_kwargs["cv"], train_score_model.TimeSeriesSplit)
        assert call_kwargs["scoring"] == "neg_root_mean_squared_error"
        assert call_kwargs["param_distributions"] == train_score_model.PARAM_DISTRIBUTIONS
        assert result == {"max_depth": 3}

    def test_parallelizes_search_without_oversubscribing_per_fit_threads(self):
        df = train_score_model._add_label(_make_df(10), "margin")
        feature_columns = train_score_model._feature_columns(df)
        X, y = df[feature_columns], df[train_score_model.LABEL_COLUMN]
        mock_search = MagicMock()
        mock_search.best_params_ = {}
        mock_search.best_score_ = -7.5

        with patch.object(train_score_model, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(train_score_model.xgb, "XGBRegressor") as mock_xgb_cls:
            train_score_model._tune_hyperparameters(X, y)

        assert mock_search_cls.call_args.kwargs["n_jobs"] == -1
        mock_xgb_cls.assert_called_once_with(objective="reg:squarederror", n_jobs=1)


class TestMain:
    def _mock_model(self):
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([5.0, -3.0])
        mock_model.get_booster.return_value.save_raw.return_value = b"fake-model-bytes"
        mock_model.get_booster.return_value.get_score.return_value = {}
        return mock_model

    def test_writes_versioned_model_under_the_targets_own_model_name(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("SCORE_TARGET", "home_score")

        df = _make_df(10)
        mock_s3 = MagicMock()
        mock_s3.get_bytes.return_value = _parquet_bytes(df)
        mock_s3.list_keys.return_value = []
        mock_s3.object_exists.return_value = False
        mock_model = self._mock_model()

        with patch.object(train_score_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_score_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_score_model.xgb, "XGBRegressor", return_value=mock_model):
            train_score_model.main()

        model_call = mock_s3.put_bytes.call_args
        assert model_call.args[0] == "nfl/home-score/v1/model.xgb"
        assert model_call.args[1] == b"fake-model-bytes"

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

    def test_gates_promotion_on_rmse(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("SCORE_TARGET", "margin")

        df = _make_df(10)
        mock_s3 = MagicMock()
        mock_s3.get_bytes.return_value = _parquet_bytes(df)
        mock_s3.list_keys.return_value = []
        mock_s3.object_exists.return_value = False
        mock_model = self._mock_model()

        with patch.object(train_score_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_score_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_score_model.xgb, "XGBRegressor", return_value=mock_model), \
             patch.object(train_score_model.model_common, "promote_if_better") as mock_promote:
            train_score_model.main()

        assert mock_promote.call_args.args[-1] == "rmse"
