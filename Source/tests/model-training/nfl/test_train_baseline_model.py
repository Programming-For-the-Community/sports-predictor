"""
Unit tests for the NFL logistic regression baseline training entrypoint.

sklearn's LogisticRegression is ALWAYS mocked here (via _build_pipeline),
same mocking boundary as test_train_model.py draws around XGBoost --
these tests verify train_baseline_model.py's own orchestration, never a
real fit.
"""
import io
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import train_baseline_model


def _make_df(n=10):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "event_date": [f"2025-09-{i + 1:02d}" for i in range(n)],
        "home_entity_id": ["KC"] * n,
        "away_entity_id": ["LAC"] * n,
        "home_elo": [1500.0 + i for i in range(n)],
        "elo_diff": [10.0] * n,
        "label_home_won": [i % 2 == 0 for i in range(n)],
        "label_home_score": [20] * n,
        "label_away_score": [17] * n,
    })


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False)
    return buffer.getvalue()


class TestTrain:
    """_tune_hyperparameters is mocked directly, same rationale as
    test_train_model.py's TestTrain -- letting a real GridSearchCV wrap a
    mocked pipeline would break sklearn's internal clone(), and would mean
    these tests exercise real cross-validation fitting."""

    def _mock_pipeline(self, coefficients=(0.5, -0.3)):
        mock_pipeline = MagicMock()
        mock_pipeline.set_params.return_value = mock_pipeline
        mock_pipeline.predict.return_value = np.array([True, False])
        mock_pipeline.predict_proba.return_value = np.array([[0.1, 0.9], [0.8, 0.2]])
        mock_pipeline.named_steps = {"model": MagicMock(coef_=np.array([list(coefficients)]))}
        return mock_pipeline

    def test_fits_mocked_pipeline_and_computes_metrics(self):
        df = _make_df(10)
        mock_pipeline = self._mock_pipeline()

        with patch.object(train_baseline_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_baseline_model, "_build_pipeline", return_value=mock_pipeline):
            model, metadata = train_baseline_model.train(df)

        mock_pipeline.fit.assert_called_once()
        assert model is mock_pipeline
        assert metadata["feature_columns"] == ["home_elo", "elo_diff"]
        assert metadata["train_rows"] == 8
        assert metadata["test_rows"] == 2

    def test_sets_tuned_hyperparameters_and_strips_pipeline_prefix(self):
        df = _make_df(10)
        mock_pipeline = self._mock_pipeline()
        fake_params = {"model__C": 1.0, "model__penalty": "l2"}

        with patch.object(train_baseline_model, "_tune_hyperparameters", return_value=fake_params) as mock_tune, \
             patch.object(train_baseline_model, "_build_pipeline", return_value=mock_pipeline):
            _, metadata = train_baseline_model.train(df)

        mock_tune.assert_called_once()
        mock_pipeline.set_params.assert_called_once_with(**fake_params)
        assert metadata["hyperparameters"] == {"C": 1.0, "penalty": "l2"}

    def test_metrics_are_plain_python_types_not_numpy(self):
        df = _make_df(10)
        mock_pipeline = self._mock_pipeline()

        with patch.object(train_baseline_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_baseline_model, "_build_pipeline", return_value=mock_pipeline):
            _, metadata = train_baseline_model.train(df)

        assert isinstance(metadata["accuracy"], float)
        assert isinstance(metadata["log_loss"], float)
        assert isinstance(metadata["train_rows"], int)
        assert isinstance(metadata["test_rows"], int)

    def test_feature_coefficients_are_signed_and_sorted_by_magnitude(self):
        # elo_diff's coefficient (-0.3) is smaller in magnitude than
        # home_elo's (0.5) despite being negative -- sort order must go by
        # absolute value, not raw signed value.
        df = _make_df(10)
        mock_pipeline = self._mock_pipeline(coefficients=(0.5, -0.3))

        with patch.object(train_baseline_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_baseline_model, "_build_pipeline", return_value=mock_pipeline):
            _, metadata = train_baseline_model.train(df)

        assert metadata["feature_coefficients"] == {"home_elo": 0.5, "elo_diff": -0.3}
        assert list(metadata["feature_coefficients"].keys()) == ["home_elo", "elo_diff"]

    def test_never_touches_real_sklearn_fit(self):
        df = _make_df(10)
        mock_pipeline = self._mock_pipeline()

        with patch.object(train_baseline_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_baseline_model, "_build_pipeline", return_value=mock_pipeline):
            train_baseline_model.train(df)

        mock_pipeline.fit.assert_called_once()  # the mock's fit(), never a real LogisticRegression


class TestTuneHyperparameters:
    """GridSearchCV is mocked at the class level, same rationale as
    test_train_model.py's TestTuneHyperparameters."""

    def test_uses_time_series_split_not_kfold(self):
        df = _make_df(10)
        X, y = df[["home_elo", "elo_diff"]], df["label_home_won"]
        mock_search = MagicMock()
        mock_search.best_params_ = {"model__C": 1.0}
        mock_search.best_score_ = -0.65

        with patch.object(train_baseline_model, "GridSearchCV", return_value=mock_search) as mock_search_cls:
            result = train_baseline_model._tune_hyperparameters(X, y)

        mock_search.fit.assert_called_once()
        call_kwargs = mock_search_cls.call_args.kwargs
        assert isinstance(call_kwargs["cv"], train_baseline_model.TimeSeriesSplit)
        assert call_kwargs["scoring"] == "neg_log_loss"
        assert call_kwargs["param_grid"] == train_baseline_model.PARAM_GRID
        assert result == {"model__C": 1.0}

    def test_enables_verbose_progress_output(self):
        df = _make_df(10)
        X, y = df[["home_elo", "elo_diff"]], df["label_home_won"]
        mock_search = MagicMock()
        mock_search.best_params_ = {}
        mock_search.best_score_ = -0.65

        with patch.object(train_baseline_model, "GridSearchCV", return_value=mock_search) as mock_search_cls:
            train_baseline_model._tune_hyperparameters(X, y)

        assert mock_search_cls.call_args.kwargs["verbose"] > 9

    def test_logs_total_fit_count_before_starting(self, caplog):
        df = _make_df(10)
        X, y = df[["home_elo", "elo_diff"]], df["label_home_won"]
        mock_search = MagicMock()
        mock_search.best_params_ = {}
        mock_search.best_score_ = -0.65

        with caplog.at_level("INFO"), \
             patch.object(train_baseline_model, "GridSearchCV", return_value=mock_search):
            train_baseline_model._tune_hyperparameters(X, y)

        expected_fits = (
            len(train_baseline_model.PARAM_GRID["model__C"])
            * len(train_baseline_model.PARAM_GRID["model__penalty"])
            * train_baseline_model.CV_SPLITS
        )
        assert any(str(expected_fits) in r.message for r in caplog.records)


class TestMain:
    def _mock_pipeline(self):
        mock_pipeline = MagicMock()
        mock_pipeline.set_params.return_value = mock_pipeline
        mock_pipeline.predict.return_value = np.array([True, False])
        mock_pipeline.predict_proba.return_value = np.array([[0.1, 0.9], [0.8, 0.2]])
        mock_pipeline.named_steps = {"model": MagicMock(coef_=np.array([[0.5, -0.3]]))}
        return mock_pipeline

    def test_writes_versioned_model_and_metadata_under_its_own_model_name(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")

        df = _make_df(10)
        mock_s3 = MagicMock()
        mock_s3.get_bytes.return_value = _parquet_bytes(df)
        mock_s3.list_keys.return_value = ["nfl/win-probability-logistic/v1/model.joblib"]
        mock_pipeline = self._mock_pipeline()

        with patch.object(train_baseline_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_baseline_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_baseline_model, "_build_pipeline", return_value=mock_pipeline), \
             patch.object(train_baseline_model, "joblib") as mock_joblib:
            mock_joblib.dump.side_effect = lambda model, buffer: buffer.write(b"fake-model-bytes")
            train_baseline_model.main()

        model_call = mock_s3.put_bytes.call_args
        assert model_call.args[0] == "nfl/win-probability-logistic/v2/model.joblib"
        assert model_call.args[1] == b"fake-model-bytes"

        model_card_call = mock_s3.put_json.call_args
        assert model_card_call.args[0] == "nfl/win-probability-logistic/v2/model_card.json"
        assert model_card_call.args[1]["version"] == 2
        assert model_card_call.args[1]["model_name"] == "win-probability-logistic"
        assert model_card_call.args[1]["algorithm"] == "logistic_regression"

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_baseline_model.main()
