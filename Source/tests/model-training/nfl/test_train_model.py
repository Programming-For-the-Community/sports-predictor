"""
Unit tests for the NFL win-probability training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_model.py's own orchestration (column selection, chronological
split, naive-baseline computation, and what gets handed to run_backtest),
not the tournament itself (see Source/tests/library/ml/test_backtest.py)
or any real algorithm fitting (see Source/tests/library/ml/
test_model_types.py). Same "never touch real training" boundary this
suite always drew, just moved up a layer now that train_model.py delegates
the actual fitting to run_backtest instead of doing it inline.
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


def _fake_result(version=1, algorithm="xgboost"):
    return {
        "winner": {"model_name": "win-probability", "algorithm": algorithm, "version": version, "log_loss": 0.6},
        "promoted": True,
        "candidates": [{"algorithm": algorithm, "log_loss": 0.6}],
    }


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


class TestTrain:
    def test_calls_run_backtest_with_both_candidates_and_classification_task(self):
        df = _make_df(10)

        with patch.object(train_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert call.kwargs["task"] == "classification"
        assert call.kwargs["candidates"] == train_model.CANDIDATES
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostClassifierAdapter", "LogisticRegressionAdapter",
            "RandomForestClassifierAdapter", "MLPClassifierAdapter",
        }
        assert result == _fake_result()

    def test_splits_chronologically_and_builds_numeric_frames_of_the_right_columns(self):
        df = _make_df(10)

        with patch.object(train_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_model.train(MagicMock(), df)

        call = mock_run.call_args
        assert list(call.kwargs["X_train"].columns) == ["home_elo", "elo_diff"]
        assert len(call.kwargs["X_train"]) == 8
        assert len(call.kwargs["X_test"]) == 2
        assert call.kwargs["y_train"].name == "label_home_won"
        # Chronological, not random -- the held-out rows are the most
        # recent by event_date.
        assert call.kwargs["X_test"].index.tolist() == df.index[-2:].tolist()

    def test_naive_baseline_accuracy_is_fraction_of_home_wins_in_test_set(self):
        # label_home_won is [True, False, True, False, ...] -- the last 2
        # rows (test_fraction=0.2, chronological) are i=8 (True) and i=9
        # (False), so "always predict home wins" gets exactly one of two
        # right.
        df = _make_df(10)

        with patch.object(train_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["naive_baseline_metrics"] == {"naive_baseline_accuracy": 0.5}

    def test_passes_row_counts_and_date_ranges_in_extra_metadata(self):
        df = _make_df(10)

        with patch.object(train_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_model.train(MagicMock(), df)

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["train_rows"] == 8
        assert extra["test_rows"] == 2
        assert "train_date_range" in extra
        assert "test_date_range" in extra

    def test_promotion_metric_is_log_loss(self):
        df = _make_df(10)

        with patch.object(train_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["promotion_metric"] == "log_loss"

    def test_all_null_feature_column_is_still_numeric_not_object(self):
        # A real production crash: weather_temperature (and, at the time,
        # a QB-average-column naming bug) came back from Parquet as all
        # None for the training window, which pandas types as `object` --
        # both XGBoost and scikit-learn raise on an object dtype column
        # even though every value is just a missing float.
        df = _make_df(10)
        df["weather_temperature"] = [None] * len(df)  # entirely null -- object dtype from Parquet

        with patch.object(train_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_model.train(MagicMock(), df)

        assert mock_run.call_args.kwargs["X_train"]["weather_temperature"].dtype == np.float64


class TestMain:
    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df = _make_df(10)
        mock_s3 = MagicMock()

        with patch.object(train_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_model, "train", return_value=_fake_result()) as mock_train:
            train_model.main()

        mock_load.assert_called_once_with(mock_s3, train_model.EVENT_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df)

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)

        with pytest.raises(KeyError):
            train_model.main()


class TestEndToEndWithRealBacktest:
    """One test that lets run_backtest itself run for real, with every
    candidate adapter's own tune_and_fit mocked (the same "never touch
    real fitting" boundary every other test in this pipeline draws) --
    proves train_model.py's arguments actually thread through
    run_backtest -> save_model_artifact -> promote_if_better correctly
    end to end, not just that train_model.py calls run_backtest with
    plausible-looking arguments in isolation."""

    def test_writes_every_candidate_and_promotes_the_best_one(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df = _make_df(10)
        mock_s3 = MagicMock()
        mock_s3.get_bytes.return_value = _parquet_bytes(df)
        # Reflects what's actually been "written" so far via put_bytes,
        # rather than a fixed empty list -- otherwise every candidate in
        # the tournament would compute the same next_model_version(1),
        # since a static mock return value doesn't track state the way
        # real S3 would across run_backtest's per-candidate save calls.
        mock_s3.list_keys.side_effect = lambda prefix: [
            call.args[0] for call in mock_s3.put_bytes.call_args_list if call.args[0].startswith(prefix)
        ]
        mock_s3.object_exists.return_value = False

        # True test-set labels are [True, False] (see _make_df) --
        # xgboost's predictions are near-perfect and confident; every
        # other candidate is directionally correct but progressively less
        # confident, so xgboost must win on log_loss unambiguously.
        candidate_predictions = {
            "XGBoostClassifierAdapter": (np.array([0.95, 0.05]), b"xgb-bytes"),
            "LogisticRegressionAdapter": (np.array([0.6, 0.4]), b"logistic-bytes"),
            "RandomForestClassifierAdapter": (np.array([0.55, 0.45]), b"rf-bytes"),
            "MLPClassifierAdapter": (np.array([0.5, 0.5]), b"mlp-bytes"),
        }
        patches = []
        for adapter in train_model.CANDIDATES:
            predictions, model_bytes = candidate_predictions[type(adapter).__name__]
            patches.append(patch.object(adapter, "tune_and_fit", return_value=(f"{adapter.algorithm}-estimator", {})))
            patches.append(patch.object(adapter, "predict", return_value=predictions))
            patches.append(patch.object(adapter, "feature_importances", return_value={}))
            patches.append(patch.object(adapter, "serialize", return_value=model_bytes))

        with patch.object(train_model, "S3Manager", return_value=mock_s3):
            for p in patches:
                p.start()
            try:
                train_model.main()
            finally:
                for p in patches:
                    p.stop()

        written_models = {call.args[0]: call.args[1] for call in mock_s3.put_bytes.call_args_list}
        assert written_models["nfl/win-probability/v1/model.xgb"] == b"xgb-bytes"
        assert written_models["nfl/win-probability/v2/model.joblib"] == b"logistic-bytes"
        assert written_models["nfl/win-probability/v3/model.joblib"] == b"rf-bytes"
        assert written_models["nfl/win-probability/v4/model.joblib"] == b"mlp-bytes"

        # xgboost's predictions ([0.95, 0.05] vs true [True, False]) are
        # perfect; logistic's ([0.6, 0.4]) are directionally right but
        # less confident -- xgboost must win on log_loss.
        promotion_call = next(c for c in mock_s3.put_json.call_args_list if c.args[0] == "nfl/win-probability/current.json")
        assert promotion_call.args[1] == {"version": 1}
