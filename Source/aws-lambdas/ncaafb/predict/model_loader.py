"""
Loads a promoted model artifact for live inference, and scores one
feature row against it. Reads the same current.json pointer and
model_card.json layout the training scripts write --
library.storage.model_artifacts is the shared, sport-agnostic
key-naming module both sides already depend on.

Dispatches on model_card["algorithm"] through library.ml.model_types.
ADAPTERS -- not hardcoded to XGBoost, since run_backtest lets several
algorithms compete for the same target and promotes whichever wins, so
the currently-promoted algorithm for any given model_name can change
from one retrain to the next.
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
    """model_name has never had a version promoted (library.ml.
    training_common.promote_if_better never wrote a current.json for it)
    -- distinct from a version existing but the artifact itself being
    missing or corrupt, which is a real bug rather than an expected
    state."""


class UnknownAlgorithmError(Exception):
    """The promoted model card names an algorithm this Lambda's own copy
    of library.ml.model_types.ADAPTERS doesn't recognize -- e.g. a newer
    training image added an algorithm this Lambda hasn't been redeployed
    with yet. A real deployment-ordering bug, not an expected state."""


def load_current_model(s3, sport: str, model_name: str):
    """Returns (estimator, model_card). model_card['feature_columns'] is
    the ordered column list the estimator was trained on -- predict()
    below builds its feature row from exactly these columns, in this
    order, since neither an XGBoost Booster nor an sklearn Pipeline has
    any other way to know what a raw byte-loaded artifact's columns
    mean. model_card['algorithm'] picks which adapter deserializes the
    artifact -- see library.ml.model_types.ADAPTERS.

    Cached at module level for _MODEL_CACHE_TTL_SECONDS -- see this
    module's own top-of-file comment. NoPromotedModelError/
    UnknownAlgorithmError are never cached -- both are rare, and caching
    a negative result would mean a model that gets promoted mid-TTL
    stays invisible to a warm container for up to _MODEL_CACHE_TTL_SECONDS
    longer than it needs to."""
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
    library.ml.training_common.numeric_frame applies at training time,
    which every adapter's underlying estimator already expects (no
    training run ever imputed a value at the feature-row level instead of
    leaving it as NaN -- LogisticRegressionAdapter's own pipeline does its
    own median imputation internally, same as at training time).

    Works identically for a classification-task target (e.g.
    win-probability -- the adapter's predict() output already is the
    probability) or a regression-task one (e.g. margin or a player-prop
    stat) -- only what the returned float means differs, and this
    function doesn't need to know which."""
    feature_columns = model_card["feature_columns"]
    row = {
        column: float(feature_row[column]) if isinstance(feature_row.get(column), (int, float)) else float("nan")
        for column in feature_columns
    }
    X = pd.DataFrame([row], columns=feature_columns)

    adapter = ADAPTERS[model_card["algorithm"]]
    return float(adapter.predict(estimator, X)[0])
