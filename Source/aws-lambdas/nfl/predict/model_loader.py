"""
Loads a promoted NFL model artifact for live inference, and scores one
feature row against it. Reads the same current.json pointer and
model_card.json layout the training scripts write.

Dispatches on model_card["algorithm"] through library.ml.model_types.
ADAPTERS, since the currently-promoted algorithm for any given
model_name can differ across models.
"""
import time

import pandas as pd

from library.ml.model_types import ADAPTERS
from library.storage.model_artifacts import current_version_key, model_artifact_key

MODEL_CARD_FILENAME = "model_card.json"

# Module-level, not per-request -- a Lambda execution environment reuses
# this module's state across every invocation it stays warm for, so a
# plain dict here means one promoted model gets fetched from S3 once per
# warm container instead of once per request. Keyed by (sport,
# model_name); TTL'd rather than cached forever so a newly-promoted
# model doesn't stay stale for a container's whole warm lifetime.
_MODEL_CACHE_TTL_SECONDS = 300
_model_cache: dict[tuple[str, str], tuple[float, tuple]] = {}


class NoPromotedModelError(Exception):
    """model_name has never had a version promoted (no current.json
    pointer exists for it) -- distinct from a version existing but the
    artifact itself being missing or corrupt."""


class UnknownAlgorithmError(Exception):
    """The promoted model card names an algorithm this Lambda's own copy
    of library.ml.model_types.ADAPTERS doesn't recognize."""


def load_current_model(s3, sport: str, model_name: str):
    """Returns (estimator, model_card). model_card['feature_columns'] is
    the ordered column list the estimator was trained on -- predict()
    below builds its feature row from exactly these columns, in this
    order. model_card['algorithm'] picks which adapter deserializes the
    artifact -- see library.ml.model_types.ADAPTERS.

    Cached at module level for _MODEL_CACHE_TTL_SECONDS.
    NoPromotedModelError/UnknownAlgorithmError are never cached."""
    cache_key = (sport, model_name)
    cached = _model_cache.get(cache_key)
    if cached is not None:
        cached_at, result = cached
        if time.monotonic() - cached_at < _MODEL_CACHE_TTL_SECONDS:
            return result

    pointer_key = current_version_key(sport, model_name)
    if not s3.object_exists(pointer_key):
        raise NoPromotedModelError(f"No promoted version exists for {sport}/{model_name}")
    version = s3.get_json(pointer_key)["version"]

    model_card = s3.get_json(model_artifact_key(sport, model_name, version, MODEL_CARD_FILENAME))
    adapter = ADAPTERS.get(model_card["algorithm"])
    if adapter is None:
        raise UnknownAlgorithmError(f"No adapter registered for algorithm {model_card['algorithm']!r}")

    model_bytes = s3.get_bytes(model_artifact_key(sport, model_name, version, adapter.artifact_filename))
    estimator = adapter.deserialize(model_bytes)
    result = (estimator, model_card)
    _model_cache[cache_key] = (time.monotonic(), result)
    return result


def predict(estimator, model_card: dict, feature_row: dict) -> float:
    """feature_row is build_live_event_features'/build_live_player_features's
    full output (features and labels mixed together, same shape training
    rows have) -- this pulls out just the columns the model was actually
    trained on. Missing/non-numeric values become NaN, the same coercion
    library.ml.training_common.numeric_frame applies at training time.

    Works identically for a classification-task target (e.g.
    win-probability) or a regression-task one (e.g. margin or a
    player-prop stat) -- only what the returned float means differs."""
    feature_columns = model_card["feature_columns"]
    row = {
        column: float(feature_row[column]) if isinstance(feature_row.get(column), (int, float)) else float("nan")
        for column in feature_columns
    }
    X = pd.DataFrame([row], columns=feature_columns)

    adapter = ADAPTERS[model_card["algorithm"]]
    return float(adapter.predict(estimator, X)[0])
