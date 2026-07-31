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
    def _mock_model(self):
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([True, False])
        mock_model.predict_proba.return_value = np.array([[0.1, 0.9], [0.8, 0.2]])
        return mock_model

    def test_fits_mocked_model_and_computes_metrics(self):
        df = _make_df(10)
        mock_model = self._mock_model()

        with patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model) as mock_cls:
            model, metadata = train_model.train(df)

        mock_cls.assert_called_once_with(objective="binary:logistic", eval_metric="logloss")
        mock_model.fit.assert_called_once()
        assert model is mock_model
        assert metadata["feature_columns"] == ["home_elo", "elo_diff"]
        assert metadata["train_rows"] == 8
        assert metadata["test_rows"] == 2

    def test_metrics_are_plain_python_types_not_numpy(self):
        # sklearn returns numpy scalar types (e.g. numpy.float64), which
        # json.dumps() can't serialize -- the same lesson learned from
        # DynamoDB's Decimal. Metadata gets written via S3Manager.put_json,
        # so this has to hold or a real run would crash writing metadata.
        df = _make_df(10)
        mock_model = self._mock_model()

        with patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
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

        with patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            train_model.train(df)

        mock_model.get_booster.assert_not_called()  # only main() calls this, not train()


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
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            train_model.main()

        model_call = mock_s3.put_bytes.call_args
        assert model_call.args[0] == "nfl/win-probability/v2/model.xgb"
        assert model_call.args[1] == b"fake-model-bytes"

        metadata_call = mock_s3.put_json.call_args
        assert metadata_call.args[0] == "nfl/win-probability/v2/metadata.json"
        assert metadata_call.args[1]["version"] == 2
        assert metadata_call.args[1]["model_name"] == "win-probability"

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
             patch.object(train_model.xgb, "XGBClassifier", return_value=mock_model):
            train_model.main()

        summary_lines = [r.message for r in caplog.records if "Training complete" in r.message]
        assert len(summary_lines) == 1
        assert "v1" in summary_lines[0]
        assert "accuracy=" in summary_lines[0]
        assert "log_loss=" in summary_lines[0]
