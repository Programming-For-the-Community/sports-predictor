"""
Loads a promoted NFL model artifact (see model_common.promote_if_better
in Source/model-training/nfl/) for live inference, and scores one feature
row against it. Reads the same current.json pointer and
model_card.json/model.xgb layout the training scripts write --
library.storage.model_artifacts is the shared, sport-agnostic key-naming
module both sides already depend on, so no duplicated key convention
exists between training and serving.

Deliberately does not import model_common.py (Source/model-training/nfl/)
-- that module lives in the training image, not the shared library
package this Lambda's zip bundles, and everything it has that inference
actually needs (current-version lookup, artifact key naming) already
lives in library.storage.model_artifacts.
"""
import xgboost as xgb

from library.storage.model_artifacts import current_version_key, model_artifact_key

MODEL_CARD_FILENAME = "model_card.json"
MODEL_FILENAME = "model.xgb"


class NoPromotedModelError(Exception):
    """model_name has never had a version promoted (model_common.
    promote_if_better never wrote a current.json for it) -- distinct
    from a version existing but the artifact itself being missing or
    corrupt, which is a real bug rather than an expected state."""


def load_current_model(s3, sport: str, model_name: str) -> tuple[xgb.Booster, dict]:
    """Returns (booster, model_card). model_card['feature_columns'] is
    the ordered column list the booster was trained on -- predict()
    below builds its DMatrix from exactly these columns, in this order,
    since XGBoost has no other way to know what a raw byte-loaded
    Booster's columns mean."""
    pointer_key = current_version_key(sport, model_name)
    if not s3.object_exists(pointer_key):
        raise NoPromotedModelError(f"No promoted version exists for {sport}/{model_name}")
    version = s3.get_json(pointer_key)["version"]

    model_card = s3.get_json(model_artifact_key(sport, model_name, version, MODEL_CARD_FILENAME))
    model_bytes = s3.get_bytes(model_artifact_key(sport, model_name, version, MODEL_FILENAME))

    booster = xgb.Booster()
    booster.load_model(bytearray(model_bytes))
    return booster, model_card


def predict(booster: xgb.Booster, model_card: dict, feature_row: dict) -> float:
    """feature_row is build_live_event_features'/build_live_player_features's
    full output (features and labels mixed together, same shape training
    rows have) -- this pulls out just the columns the model was actually
    trained on. Missing/non-numeric values become NaN, the same coercion
    model_common.numeric_frame applies at training time, which XGBoost's
    own missing-value handling already expects (no training run ever
    imputed a value instead of leaving it as NaN).

    Works identically for a classifier (binary:logistic, e.g.
    win-probability -- the raw Booster output for that objective already
    is the probability) or a regressor (reg:squarederror, e.g. margin or
    a player-prop stat) -- the raw Booster.predict() call is the same
    either way, only what the returned float means differs."""
    feature_columns = model_card["feature_columns"]
    row = []
    for column in feature_columns:
        value = feature_row.get(column)
        row.append(float(value) if isinstance(value, (int, float)) else float("nan"))
    matrix = xgb.DMatrix([row], feature_names=feature_columns)
    return float(booster.predict(matrix)[0])
