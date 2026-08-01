"""
Unit tests for the NFL win-probability training entrypoint.

xgboost.XGBClassifier is ALWAYS mocked here -- these tests verify
train_model.py's own orchestration (column selection, chronological
split, metric computation, versioned S3 writes), never fitting a real
model. Same boundary the rest of this pipeline's tests already draw
around AWS calls (mocked boto3/DynamoDB/S3) -- a test run should never be
able to accidentally kick off real model training any more than it can
accidentally hit real AWS.
"""
import io
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import train_model


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


class TestFeatureColumns:
    def test_excludes_identifiers_and_every_label_column(self):
        df = _make_df()

        columns = train_model._feature_columns(df)

        assert columns == ["home_elo", "elo_diff"]

    def test_excludes_venue_city_and_state(self):
        # venue_indoor/weather_temperature ARE real feature inputs, but raw
        # city/state strings aren't model-consumable without encoding --
        # see design/DATA_SCHEMA.md's events table notes.
        df = _make_df()
        df["venue_indoor"] = [False] * len(df)
        df["venue_city"] = ["Kansas City"] * len(df)
        df["venue_state"] = ["MO"] * len(df)

        columns = train_model._feature_columns(df)

        assert "venue_indoor" in columns
        assert "venue_city" not in columns
        assert "venue_state" not in columns


class TestChronologicalSplit:
    def test_splits_by_date_not_randomly(self):
        df = _make_df(10)

        train_df, test_df = train_model._chronological_split(df, test_fraction=0.2)

        assert len(train_df) == 8
        assert len(test_df) == 2
        assert train_df["event_date"].max() < test_df["event_date"].min()

    def test_works_on_unsorted_input(self):
        df = _make_df(10).sample(frac=1, random_state=0)  # shuffle rows

        train_df, test_df = train_model._chronological_split(df, test_fraction=0.2)

        assert train_df["event_date"].max() < test_df["event_date"].min()


class TestTrain:
    """_tune_hyperparameters is mocked directly (returning a fixed, empty
    params dict) rather than letting it run for real here -- letting a
    real RandomizedSearchCV wrap a mocked XGBClassifier would break (
    sklearn's internal clone() doesn't work on a MagicMock), and even if
    it didn't, it would mean these tests exercise real cross-validation
    fitting, which is exactly the "accidentally train a real model"
    outcome this suite is built to avoid. See TestTuneHyperparameters for
    coverage of _tune_hyperparameters' own configuration."""

    def _mock_model(self):
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([True, False])
        mock_model.predict_proba.return_value = np.array([[0.1, 0.9], [0.8, 0.2]])
        mock_model.get_booster.return_value.get_score.return_value = {}
        return mock_model

    def test_fits_mocked_model_and_computes_metrics(self):
        df = _make_df(10)
        mock_model = self._mock_model()

        with patch.object(train_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model) as mock_cls:
            model, metadata = train_model.train(df)

        mock_cls.assert_called_once_with(objective="binary:logistic", eval_metric="logloss")
        mock_model.fit.assert_called_once()
        assert model is mock_model
        assert metadata["feature_columns"] == ["home_elo", "elo_diff"]
        assert metadata["train_rows"] == 8
        assert metadata["test_rows"] == 2

    def test_includes_tuned_hyperparameters_in_metadata(self):
        df = _make_df(10)
        mock_model = self._mock_model()
        fake_params = {"max_depth": 3, "n_estimators": 100}

        with patch.object(train_model, "_tune_hyperparameters", return_value=fake_params) as mock_tune, \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model) as mock_cls:
            _, metadata = train_model.train(df)

        mock_tune.assert_called_once()
        mock_cls.assert_called_once_with(objective="binary:logistic", eval_metric="logloss", **fake_params)
        assert metadata["hyperparameters"] == fake_params

    def test_metrics_are_plain_python_types_not_numpy(self):
        # sklearn returns numpy scalar types (e.g. numpy.float64), which
        # json.dumps() can't serialize -- the same lesson learned from
        # DynamoDB's Decimal. Metadata gets written via S3Manager.put_json,
        # so this has to hold or a real run would crash writing metadata.
        df = _make_df(10)
        mock_model = self._mock_model()

        with patch.object(train_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            _, metadata = train_model.train(df)

        assert isinstance(metadata["accuracy"], float)
        assert isinstance(metadata["log_loss"], float)
        assert isinstance(metadata["train_rows"], int)
        assert isinstance(metadata["test_rows"], int)

    def test_all_null_feature_column_is_still_numeric_not_object(self):
        # A real production crash: weather_temperature (and, at the time,
        # a QB-average-column naming bug) came back from Parquet as all
        # None for the training window, which pandas types as `object` --
        # XGBoost raises ValueError on an object dtype column even though
        # every value is just a missing float. train() must coerce feature
        # columns to numeric regardless of how sparse any one of them is.
        df = _make_df(10)
        df["weather_temperature"] = [None] * len(df)  # entirely null -- object dtype from Parquet
        mock_model = self._mock_model()
        captured = {}

        def _capture_fit(X, y):
            captured["dtype"] = X["weather_temperature"].dtype
        mock_model.fit.side_effect = _capture_fit

        with patch.object(train_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            train_model.train(df)

        assert captured["dtype"] == np.float64

    def test_never_touches_real_xgboost_training(self):
        # Sanity check on the mocking boundary itself: fit() must be the
        # mock's fit(), not a real Booster ever getting built.
        df = _make_df(10)
        mock_model = self._mock_model()

        with patch.object(train_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            train_model.train(df)

        mock_model.fit.assert_called_once()  # the mock's fit(), never a real Booster

    def test_feature_importances_use_gain_and_default_unused_features_to_zero(self):
        df = _make_df(10)
        mock_model = self._mock_model()
        # "elo_diff" never appears in get_score()'s output -- as if XGBoost
        # never split on it -- while "home_elo" does.
        mock_model.get_booster.return_value.get_score.return_value = {"home_elo": 12.5}

        with patch.object(train_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            _, metadata = train_model.train(df)

        mock_model.get_booster.return_value.get_score.assert_called_once_with(importance_type="gain")
        assert metadata["feature_importances"] == {"home_elo": 12.5, "elo_diff": 0.0}
        assert isinstance(metadata["feature_importances"]["home_elo"], float)

    def test_feature_importances_are_sorted_descending(self):
        df = _make_df(10)
        mock_model = self._mock_model()
        mock_model.get_booster.return_value.get_score.return_value = {"home_elo": 3.0, "elo_diff": 9.0}

        with patch.object(train_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            _, metadata = train_model.train(df)

        assert list(metadata["feature_importances"].keys()) == ["elo_diff", "home_elo"]


class TestTuneHyperparameters:
    """RandomizedSearchCV itself is mocked at the class level -- this
    verifies _tune_hyperparameters configures the search correctly
    (TimeSeriesSplit, not k-fold; scoring="neg_log_loss") without ever
    letting a real search run, since RandomizedSearchCV.fit() would mean
    real XGBoost fits across every param combination and CV fold."""

    def test_uses_time_series_split_not_kfold(self):
        df = _make_df(10)
        X, y = df[["home_elo", "elo_diff"]], df["label_home_won"]
        mock_search = MagicMock()
        mock_search.best_params_ = {"max_depth": 3}
        mock_search.best_score_ = -0.65

        with patch.object(train_model, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls:
            result = train_model._tune_hyperparameters(X, y)

        mock_search.fit.assert_called_once()
        call_kwargs = mock_search_cls.call_args.kwargs
        assert isinstance(call_kwargs["cv"], train_model.TimeSeriesSplit)
        assert call_kwargs["scoring"] == "neg_log_loss"
        assert call_kwargs["param_distributions"] == train_model.PARAM_DISTRIBUTIONS
        assert result == {"max_depth": 3}

    def test_parallelizes_search_without_oversubscribing_per_fit_threads(self):
        df = _make_df(10)
        X, y = df[["home_elo", "elo_diff"]], df["label_home_won"]
        mock_search = MagicMock()
        mock_search.best_params_ = {}
        mock_search.best_score_ = -0.65

        with patch.object(train_model, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(train_model.xgb, "XGBClassifier") as mock_xgb_cls:
            train_model._tune_hyperparameters(X, y)

        assert mock_search_cls.call_args.kwargs["n_jobs"] == -1
        mock_xgb_cls.assert_called_once_with(objective="binary:logistic", eval_metric="logloss", n_jobs=1)

    def test_enables_verbose_progress_output(self):
        # RandomizedSearchCV's own verbose logging is the only progress
        # visibility during the search itself (hundreds of fits, no
        # per-candidate hook to log through otherwise).
        df = _make_df(10)
        X, y = df[["home_elo", "elo_diff"]], df["label_home_won"]
        mock_search = MagicMock()
        mock_search.best_params_ = {}
        mock_search.best_score_ = -0.65

        with patch.object(train_model, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls:
            train_model._tune_hyperparameters(X, y)

        assert mock_search_cls.call_args.kwargs["verbose"] == 2

    def test_logs_total_fit_count_before_starting(self, caplog):
        df = _make_df(10)
        X, y = df[["home_elo", "elo_diff"]], df["label_home_won"]
        mock_search = MagicMock()
        mock_search.best_params_ = {}
        mock_search.best_score_ = -0.65

        with caplog.at_level("INFO"), \
             patch.object(train_model, "RandomizedSearchCV", return_value=mock_search):
            train_model._tune_hyperparameters(X, y)

        expected_fits = train_model.SEARCH_ITERATIONS * train_model.CV_SPLITS
        assert any(str(expected_fits) in r.message for r in caplog.records)


class TestMain:
    def test_writes_versioned_model_and_metadata(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")

        df = _make_df(10)
        mock_s3 = MagicMock()
        mock_s3.get_bytes.return_value = _parquet_bytes(df)
        mock_s3.list_keys.return_value = ["nfl/win-probability/v1/model.xgb"]

        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([True, False])
        mock_model.predict_proba.return_value = np.array([[0.1, 0.9], [0.8, 0.2]])
        mock_model.get_booster.return_value.save_raw.return_value = b"fake-model-bytes"
        mock_model.get_booster.return_value.get_score.return_value = {}

        with patch.object(train_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            train_model.main()

        model_call = mock_s3.put_bytes.call_args
        assert model_call.args[0] == "nfl/win-probability/v2/model.xgb"
        assert model_call.args[1] == b"fake-model-bytes"

        model_card_call = mock_s3.put_json.call_args
        assert model_card_call.args[0] == "nfl/win-probability/v2/model_card.json"
        assert model_card_call.args[1]["version"] == 2
        assert model_card_call.args[1]["model_name"] == "win-probability"
        assert "train_date_range" in model_card_call.args[1]
        assert "test_date_range" in model_card_call.args[1]

    def test_starts_at_version_one_when_none_exist(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")

        df = _make_df(10)
        mock_s3 = MagicMock()
        mock_s3.get_bytes.return_value = _parquet_bytes(df)
        mock_s3.list_keys.return_value = []

        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([True, False])
        mock_model.predict_proba.return_value = np.array([[0.1, 0.9], [0.8, 0.2]])
        mock_model.get_booster.return_value.save_raw.return_value = b"fake-model-bytes"
        mock_model.get_booster.return_value.get_score.return_value = {}

        with patch.object(train_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            train_model.main()

        assert mock_s3.put_bytes.call_args.args[0] == "nfl/win-probability/v1/model.xgb"

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_model.main()

    def test_logs_version_and_score_together(self, monkeypatch, caplog):
        # There's no backtesting harness yet -- this log line is the
        # actual, current way to see how a run performed, so it has to
        # exist and has to name both the version and the score.
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")

        df = _make_df(10)
        mock_s3 = MagicMock()
        mock_s3.get_bytes.return_value = _parquet_bytes(df)
        mock_s3.list_keys.return_value = []

        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([True, False])
        mock_model.predict_proba.return_value = np.array([[0.1, 0.9], [0.8, 0.2]])
        mock_model.get_booster.return_value.save_raw.return_value = b"fake-model-bytes"
        mock_model.get_booster.return_value.get_score.return_value = {}

        with caplog.at_level("INFO"), \
             patch.object(train_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            train_model.main()

        summary_lines = [r.message for r in caplog.records if "Training complete" in r.message]
        assert len(summary_lines) == 1
        assert "v1" in summary_lines[0]
        assert "accuracy=" in summary_lines[0]
        assert "log_loss=" in summary_lines[0]
