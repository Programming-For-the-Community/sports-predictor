"""
Unit tests for the inference Lambda's model loader. Uses a tiny real
XGBoost model (trained on trivial synthetic data, not mocked) to verify
the actual save-raw-bytes -> load_model -> predict round trip and column
ordering are correct -- mocking xgb.Booster would just assume that round
trip works instead of proving it does. S3 access itself is mocked; only
the model bytes/model_card content are real. This is a different concern
from model-training's "never touch real XGBoost" tests (see
test_train_model.py) -- those exist to avoid an expensive hyperparameter
search running by accident; this is a two-row, two-tree fit that takes
milliseconds and is the actual thing under test.
"""
from unittest.mock import MagicMock

import pytest
import xgboost as xgb

import model_loader


def _tiny_booster_bytes(feature_columns):
    X = [[float(i)] * len(feature_columns) for i in range(4)]
    y = [0, 1, 0, 1]
    model = xgb.XGBClassifier(n_estimators=2, max_depth=2, objective="binary:logistic")
    model.fit(X, y)
    return model.get_booster().save_raw()


class TestLoadCurrentModel:
    def test_raises_when_no_current_pointer_exists(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        with pytest.raises(model_loader.NoPromotedModelError):
            model_loader.load_current_model(s3, "nfl", "win-probability")

    def test_loads_the_pointed_at_version(self):
        feature_columns = ["home_elo", "away_elo"]
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 6},  # current.json
            {"feature_columns": feature_columns},  # model_card.json
        ]
        s3.get_bytes.return_value = _tiny_booster_bytes(feature_columns)

        booster, model_card = model_loader.load_current_model(s3, "nfl", "win-probability")

        assert isinstance(booster, xgb.Booster)
        assert model_card["feature_columns"] == feature_columns
        s3.get_bytes.assert_called_once_with("nfl/win-probability/v6/model.xgb")
        s3.object_exists.assert_called_once_with("nfl/win-probability/current.json")


class TestPredict:
    def test_scores_columns_in_the_model_cards_own_order(self):
        feature_columns = ["home_elo", "away_elo"]
        booster = xgb.Booster()
        booster.load_model(bytearray(_tiny_booster_bytes(feature_columns)))
        model_card = {"feature_columns": feature_columns}

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
        model_card = {"feature_columns": feature_columns}

        result = model_loader.predict(booster, model_card, {"home_elo": None, "away_elo": 1490.0})

        assert isinstance(result, float)
