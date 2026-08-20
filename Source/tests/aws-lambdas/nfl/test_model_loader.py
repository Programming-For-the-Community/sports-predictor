"""
Unit tests for the inference Lambda's model loader. Uses tiny real
XGBoost/scikit-learn models (trained on trivial synthetic data, not
mocked) to verify the actual serialize -> deserialize -> predict round
trip and column ordering are correct for both algorithms this Lambda can
be asked to serve -- see library/ml/model_types.py's ADAPTERS registry,
which model_loader.py dispatches through instead of hardcoding XGBoost.
S3 access itself is mocked; only the model bytes/model_card content are
real.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import xgboost as xgb

import model_loader
from library.ml.model_types import LogisticRegressionAdapter


@pytest.fixture(autouse=True)
def _clear_model_cache():
    """model_loader._model_cache is module-level, so it'd otherwise
    survive across tests too: every test in this file loads
    "nfl"/"win-probability", so without this, whichever test runs first
    would populate the cache and every test after it would silently skip
    its own mocked S3 calls."""
    model_loader._model_cache.clear()
    yield
    model_loader._model_cache.clear()


def _tiny_booster_bytes(feature_columns):
    """Raw xgb.train()/DMatrix API, not the sklearn wrapper -- matches
    what XGBoostAdapter's own tune_and_fit hands off to (get_booster()),
    and what model_loader.py's promoted artifacts actually are on disk."""
    X = [[float(i)] * len(feature_columns) for i in range(4)]
    y = [0, 1, 0, 1]
    dtrain = xgb.DMatrix(X, label=y, feature_names=feature_columns)
    booster = xgb.train({"objective": "binary:logistic", "max_depth": 2}, dtrain, num_boost_round=2)
    return booster.save_raw()


def _tiny_logistic_bytes(feature_columns):
    adapter = LogisticRegressionAdapter()
    X = pd.DataFrame({col: [float(i) for i in range(8)] for col in feature_columns})
    y = pd.Series([0, 1, 0, 1, 0, 1, 0, 1])
    pipeline = adapter._build_pipeline()
    pipeline.fit(X, y)
    return adapter.serialize(pipeline)


class TestLoadCurrentModel:
    def test_raises_when_no_current_pointer_exists(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        with pytest.raises(model_loader.NoPromotedModelError):
            model_loader.load_current_model(s3, "nfl", "win-probability")

    def test_loads_the_pointed_at_xgboost_version(self):
        feature_columns = ["home_elo", "away_elo"]
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 6},  # current.json
            {"algorithm": "xgboost", "feature_columns": feature_columns},  # model_card.json
        ]
        s3.get_bytes.return_value = _tiny_booster_bytes(feature_columns)

        estimator, model_card = model_loader.load_current_model(s3, "nfl", "win-probability")

        assert isinstance(estimator, xgb.Booster)
        assert model_card["feature_columns"] == feature_columns
        s3.get_bytes.assert_called_once_with("nfl/win-probability/v6/model.xgb")
        s3.object_exists.assert_called_once_with("nfl/win-probability/current.json")

    def test_loads_the_pointed_at_logistic_regression_version(self):
        # Confirms model_loader.py dispatches by algorithm rather than
        # assuming XGBoost.
        feature_columns = ["home_elo", "away_elo"]
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 8},
            {"algorithm": "logistic_regression", "feature_columns": feature_columns},
        ]
        s3.get_bytes.return_value = _tiny_logistic_bytes(feature_columns)

        estimator, model_card = model_loader.load_current_model(s3, "nfl", "win-probability")

        assert hasattr(estimator, "predict_proba")
        s3.get_bytes.assert_called_once_with("nfl/win-probability/v8/model.joblib")

    def test_a_second_call_within_the_ttl_does_not_hit_s3_again(self):
        feature_columns = ["home_elo", "away_elo"]
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 6},
            {"algorithm": "xgboost", "feature_columns": feature_columns},
        ]
        s3.get_bytes.return_value = _tiny_booster_bytes(feature_columns)

        first = model_loader.load_current_model(s3, "nfl", "win-probability")
        second = model_loader.load_current_model(s3, "nfl", "win-probability")

        assert first is second
        s3.get_bytes.assert_called_once()
        s3.object_exists.assert_called_once()

    def test_a_call_after_the_ttl_expires_hits_s3_again(self):
        feature_columns = ["home_elo", "away_elo"]
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 6},
            {"algorithm": "xgboost", "feature_columns": feature_columns},
            {"version": 7},
            {"algorithm": "xgboost", "feature_columns": feature_columns},
        ]
        s3.get_bytes.return_value = _tiny_booster_bytes(feature_columns)

        with patch("model_loader.time.monotonic", side_effect=[0.0, 0.0]):
            model_loader.load_current_model(s3, "nfl", "win-probability")
        with patch("model_loader.time.monotonic", side_effect=[model_loader._MODEL_CACHE_TTL_SECONDS + 1, 0.0]):
            model_loader.load_current_model(s3, "nfl", "win-probability")

        assert s3.get_bytes.call_count == 2
        assert s3.object_exists.call_count == 2

    def test_different_model_names_are_cached_independently(self):
        feature_columns = ["home_elo", "away_elo"]
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 6},
            {"algorithm": "xgboost", "feature_columns": feature_columns},
            {"version": 3},
            {"algorithm": "xgboost", "feature_columns": feature_columns},
        ]
        s3.get_bytes.return_value = _tiny_booster_bytes(feature_columns)

        model_loader.load_current_model(s3, "nfl", "win-probability")
        model_loader.load_current_model(s3, "nfl", "margin")

        assert s3.get_bytes.call_count == 2

    def test_raises_on_an_algorithm_this_lambda_doesnt_recognize(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 1},
            {"algorithm": "some-future-algorithm", "feature_columns": []},
        ]

        with pytest.raises(model_loader.UnknownAlgorithmError):
            model_loader.load_current_model(s3, "nfl", "win-probability")


class TestPredict:
    def test_scores_columns_in_the_model_cards_own_order(self):
        feature_columns = ["home_elo", "away_elo"]
        booster = xgb.Booster()
        booster.load_model(bytearray(_tiny_booster_bytes(feature_columns)))
        model_card = {"algorithm": "xgboost", "feature_columns": feature_columns}

        # Extra keys in feature_row that aren't in feature_columns (e.g.
        # label_* fields, venue_city) must be ignored, not fed to the model.
        result = model_loader.predict(booster, model_card, {
            "home_elo": 1550.0, "away_elo": 1490.0, "label_home_won": True, "venue_city": "Kansas City",
        })

        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_missing_or_non_numeric_values_become_nan_not_a_crash(self):
        feature_columns = ["home_elo", "away_elo"]
        booster = xgb.Booster()
        booster.load_model(bytearray(_tiny_booster_bytes(feature_columns)))
        model_card = {"algorithm": "xgboost", "feature_columns": feature_columns}

        result = model_loader.predict(booster, model_card, {"home_elo": None, "away_elo": 1490.0})

        assert isinstance(result, float)

    def test_scores_a_logistic_regression_model_through_the_same_function(self):
        feature_columns = ["home_elo", "away_elo"]
        adapter = LogisticRegressionAdapter()
        pipeline = adapter.deserialize(_tiny_logistic_bytes(feature_columns))
        model_card = {"algorithm": "logistic_regression", "feature_columns": feature_columns}

        result = model_loader.predict(pipeline, model_card, {"home_elo": 3.0, "away_elo": 1.0})

        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0
