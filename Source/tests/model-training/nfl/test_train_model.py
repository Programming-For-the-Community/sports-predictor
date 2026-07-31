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

    def test_never_touches_real_xgboost_training(self):
        # Sanity check on the mocking boundary itself: fit() must be the
        # mock's fit(), not a real Booster ever getting built.
        df = _make_df(10)
        mock_model = self._mock_model()

        with patch.object(train_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            train_model.train(df)

        mock_model.get_booster.assert_not_called()  # only main() calls this, not train()


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
