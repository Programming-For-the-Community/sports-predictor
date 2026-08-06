"""
Unit tests for library/ml/training_common.py -- moved and adapted from
Source/tests/model-training/nfl/test_model_common.py now that `sport` is
an explicit parameter instead of a hardcoded module constant (see
training_common.py's own docstring for why). evaluate_holdout/
evaluate_regression_holdout now take raw predictions/probabilities
instead of a model object -- covered directly here rather than only
indirectly through a training script's own tests, since they're no
longer coupled to any one script.
"""
import io
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pyarrow

from library.ml import training_common


def _valid_parquet_bytes() -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame({"a": [1, 2]}).to_parquet(buffer)
    return buffer.getvalue()


class TestLoadFeatures:
    def test_returns_the_dataframe_on_a_clean_read(self):
        s3 = MagicMock()
        s3.get_bytes.return_value = _valid_parquet_bytes()

        df = training_common.load_features(s3, "nfl/training-data/event_features.parquet")

        assert list(df["a"]) == [1, 2]
        s3.get_bytes.assert_called_once()

    def test_retries_once_on_a_corrupt_read_then_succeeds(self):
        s3 = MagicMock()
        s3.get_bytes.side_effect = [b"not a parquet file", _valid_parquet_bytes()]

        with patch("library.ml.training_common.time.sleep"):
            df = training_common.load_features(s3, "nfl/training-data/event_features.parquet")

        assert list(df["a"]) == [1, 2]
        assert s3.get_bytes.call_count == 2

    def test_raises_after_exhausting_every_attempt(self):
        s3 = MagicMock()
        s3.get_bytes.return_value = b"not a parquet file"

        with patch("library.ml.training_common.time.sleep"):
            try:
                training_common.load_features(s3, "nfl/training-data/event_features.parquet")
                assert False, "expected RuntimeError"
            except RuntimeError as exc:
                assert isinstance(exc.__cause__, pyarrow.ArrowInvalid)

        assert s3.get_bytes.call_count == training_common.LOAD_FEATURES_MAX_ATTEMPTS


class TestEvaluateHoldout:
    def test_computes_accuracy_and_log_loss_from_probabilities(self):
        probabilities = np.array([0.9, 0.1, 0.8, 0.2])
        y_test = [1, 0, 0, 0]  # 3rd row wrong at the 0.5 threshold

        metrics = training_common.evaluate_holdout(probabilities, y_test)

        assert metrics["accuracy"] == 0.75
        assert isinstance(metrics["log_loss"], float)

    def test_thresholds_at_point_five(self):
        probabilities = np.array([0.5, 0.49])
        y_test = [1, 0]

        metrics = training_common.evaluate_holdout(probabilities, y_test)

        assert metrics["accuracy"] == 1.0


class TestEvaluateRegressionHoldout:
    def test_computes_rmse_and_mae(self):
        predictions = np.array([10.0, 20.0])
        y_test = [12.0, 18.0]

        metrics = training_common.evaluate_regression_holdout(predictions, y_test)

        assert metrics["mae"] == 2.0
        assert isinstance(metrics["rmse"], float)


class TestGetCurrentVersion:
    def test_returns_none_when_no_pointer_exists(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = False

        assert training_common.get_current_version(mock_s3, "nfl", "win-probability") is None

    def test_returns_the_pointed_at_version(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 4}

        version = training_common.get_current_version(mock_s3, "nfl", "win-probability")

        assert version == 4
        mock_s3.get_json.assert_called_once_with("nfl/win-probability/current.json")

    def test_scopes_the_pointer_key_to_the_given_sport(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 1}

        training_common.get_current_version(mock_s3, "nba", "win-probability")

        mock_s3.get_json.assert_called_once_with("nba/win-probability/current.json")


class TestPromoteIfBetter:
    def test_promotes_directly_when_nothing_is_currently_promoted(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = False

        promoted = training_common.promote_if_better(mock_s3, "nfl", "win-probability", 1, {"log_loss": 0.65}, "log_loss")

        assert promoted is True
        mock_s3.put_json.assert_called_once_with("nfl/win-probability/current.json", {"version": 1})

    def test_promotes_when_strictly_better_than_current(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.65}

        promoted = training_common.promote_if_better(mock_s3, "nfl", "win-probability", 6, {"log_loss": 0.60}, "log_loss")

        assert promoted is True
        mock_s3.put_json.assert_called_once_with("nfl/win-probability/current.json", {"version": 6})

    def test_promotes_when_within_tolerance_but_slightly_worse(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.620}

        promoted = training_common.promote_if_better(mock_s3, "nfl", "win-probability", 6, {"log_loss": 0.626}, "log_loss")

        assert promoted is True

    def test_holds_back_a_meaningful_regression(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.60}

        promoted = training_common.promote_if_better(mock_s3, "nfl", "win-probability", 6, {"log_loss": 0.70}, "log_loss")

        assert promoted is False
        mock_s3.put_json.assert_not_called()

    def test_reads_the_current_versions_own_model_card_for_comparison(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.62}

        training_common.promote_if_better(mock_s3, "nfl", "win-probability", 6, {"log_loss": 0.61}, "log_loss")

        mock_s3.get_json.assert_called_with("nfl/win-probability/v5/model_card.json")

    def test_gate_metric_is_parameterized_not_hardcoded_to_log_loss(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 1, "rmse": 40.0}

        promoted = training_common.promote_if_better(
            mock_s3, "nfl", "player-prop-passing-yards", 2, {"rmse": 45.0}, "rmse",
        )

        assert promoted is False
        mock_s3.put_json.assert_not_called()

    def test_compares_across_different_algorithms_with_no_special_casing(self):
        # The currently-promoted version was xgboost; the challenger is
        # logistic_regression -- promote_if_better only ever compares the
        # two versions' own metric value, never algorithm identity.
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "algorithm": "xgboost", "log_loss": 0.65}

        promoted = training_common.promote_if_better(
            mock_s3, "nfl", "win-probability", 6, {"algorithm": "logistic_regression", "log_loss": 0.60}, "log_loss",
        )

        assert promoted is True

    def test_scopes_the_pointer_key_to_the_given_sport(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = False

        training_common.promote_if_better(mock_s3, "nba", "win-probability", 1, {"log_loss": 0.65}, "log_loss")

        mock_s3.put_json.assert_called_once_with("nba/win-probability/current.json", {"version": 1})
