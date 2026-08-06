"""
Shared plumbing for every sport's model-training scripts -- loading a
training set, splitting it chronologically, coercing feature dtypes,
computing holdout metrics, and writing a versioned model artifact plus
model card are identical regardless of sport, algorithm, or which
dataset/label a script targets. Moved here from
Source/model-training/nfl/model_common.py, which had no NFL-specific
assumptions except a hardcoded SPORT constant -- every function below
takes `sport` explicitly instead, so a future sport's training scripts
import this exact module rather than reaching across a sport boundary or
copy-pasting it (see design/PROJECT_PLAN.md Phase 4).

evaluate_holdout/evaluate_regression_holdout take already-computed
predictions/probabilities rather than a model object -- library.ml.
model_types.ModelAdapter.predict() is what produces those, uniformly
across every algorithm, so this module never needs to know how any
particular algorithm turns a feature matrix into a prediction.
"""
import io
import logging
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, log_loss, mean_absolute_error, root_mean_squared_error

from library.aws.s3_manager import S3Manager
from library.storage.model_artifacts import (
    current_version_key,
    model_artifact_key,
    model_artifact_prefix,
    next_model_version,
)

logger = logging.getLogger("model-training")

LOAD_FEATURES_MAX_ATTEMPTS = 3
LOAD_FEATURES_BACKOFF_SECONDS = 2.0

MODEL_CARD_FILENAME = "model_card.json"

# How much worse (as a fraction of the current production version's gate
# metric -- log_loss for a classifier, rmse for a regressor, always a
# lower-is-better metric) a newly trained version is allowed to be and
# still get promoted automatically. Set to catch a genuine regression (a
# bug, or a hyperparameter search landing somewhere pathological, e.g.
# unfloored max_depth landing on a depth-1 decision stump), not to
# adjudicate between two versions within normal run-to-run noise.
PROMOTION_TOLERANCE = 0.02

# Fraction of events (most recent, by date) held out for evaluation.
# Chronological, not random -- a random split would leak future games
# into training in a way that doesn't match how this model actually gets
# used: predicting games that haven't happened yet from ones that have.
TEST_FRACTION = 0.2


def load_features(s3: S3Manager, key: str) -> pd.DataFrame:
    """Retries on a truncated/corrupt read (pyarrow.ArrowInvalid) rather
    than failing the whole training run over one bad S3 GET -- seen in
    practice for several concurrent training tasks reading the same key
    (Terraform's TrainAllTargets Map state), transient enough that a
    second attempt reads the object cleanly.

    Imports pyarrow locally rather than at module level -- it's not one
    of library's own declared dependencies (see pyproject.toml), only its
    training-script callers' own requirements.txt, and a module-level
    import would make every caller of this module need it installed just
    to import training_common at all, not just to call this function."""
    import pyarrow

    last_exc: Exception | None = None
    for attempt in range(1, LOAD_FEATURES_MAX_ATTEMPTS + 1):
        data = s3.get_bytes(key)
        try:
            return pd.read_parquet(io.BytesIO(data))
        except pyarrow.ArrowInvalid as exc:
            last_exc = exc
            logger.warning(
                "Failed reading %s as parquet (attempt %d/%d): %s -- retrying",
                key, attempt, LOAD_FEATURES_MAX_ATTEMPTS, exc,
            )
            if attempt < LOAD_FEATURES_MAX_ATTEMPTS:
                time.sleep(LOAD_FEATURES_BACKOFF_SECONDS)
    raise RuntimeError(f"Failed reading {key} as parquet after {LOAD_FEATURES_MAX_ATTEMPTS} attempts") from last_exc


def feature_columns(df: pd.DataFrame, non_feature_columns: set[str]) -> list[str]:
    return [col for col in df.columns if col not in non_feature_columns and not col.startswith("label_")]


def chronological_split(df: pd.DataFrame, test_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = df.sort_values("event_date")
    split_index = int(len(ordered) * (1 - test_fraction))
    return ordered.iloc[:split_index], ordered.iloc[split_index:]


def numeric_frame(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """A feature column that's entirely null in the training window (e.g.
    weather_temperature, which ESPN history frequently doesn't report) has
    no non-null value for pandas to infer a numeric dtype from, so it
    comes back from Parquet as dtype `object` rather than float64 -- and
    both XGBoost and scikit-learn reject `object` columns outright, even
    when every value is just a missing float. Coercing explicitly (bools
    included) guarantees every feature column is numeric before it ever
    reaches a model, regardless of how sparse any single column is."""
    return df[columns].apply(pd.to_numeric, errors="coerce")


def evaluate_holdout(probabilities, y_test: pd.Series) -> dict:
    """probabilities: a ModelAdapter.predict() output for a
    classification-task target -- positive-class probability per row.
    Accuracy needs a thresholded label, not a probability; 0.5 is the
    standard decision boundary and the only one any caller here has ever
    used."""
    predictions = np.asarray(probabilities) >= 0.5
    return {
        # float() casts -- sklearn returns numpy scalar types, which
        # json.dumps() (via S3Manager.put_json) can't serialize.
        "accuracy": float(accuracy_score(y_test, predictions)),
        "log_loss": float(log_loss(y_test, probabilities)),
    }


def evaluate_regression_holdout(predictions, y_test: pd.Series) -> dict:
    """Regression counterpart to evaluate_holdout -- for a target
    predicting a continuous value, not a win/loss classification, so
    accuracy/log_loss don't apply. rmse is the gate metric (see
    PROMOTION_TOLERANCE); mae is carried on the model card alongside it
    since it's easier to read in the target's own units without RMSE's
    squared-error weighting toward big misses."""
    return {
        "rmse": float(root_mean_squared_error(y_test, predictions)),
        "mae": float(mean_absolute_error(y_test, predictions)),
    }


def save_model_artifact(
    s3: S3Manager,
    sport: str,
    model_name: str,
    algorithm: str,
    model_bytes: bytes,
    artifact_filename: str,
    metadata: dict,
    summary_metrics: list[str],
) -> dict:
    """Writes a versioned model artifact plus its model card -- everything
    needed to know what a given version is (training window, row counts,
    hyperparameters, holdout metrics) without re-running anything or
    cross-referencing CloudWatch Logs. See library.storage.model_artifacts
    for the versioning scheme -- each (sport, model_name) pair (e.g.
    nfl/win-probability, nfl/win-probability-logistic,
    nfl/player-prop-passing-yards) versions independently under its own
    prefix, so one model's runs never collide with or skip another's
    version numbers, and different algorithms trained for the SAME target
    (see library.ml.backtest.run_backtest) simply share that target's one
    version counter -- nothing here needs to know or care that v7 was
    xgboost and v8 was logistic_regression.

    summary_metrics: which metadata keys (e.g. ["accuracy", "log_loss"]
    or ["rmse", "mae"]) to fold into the one-line CloudWatch summary
    below -- the metric names vary by task type, everything else about
    writing the artifact doesn't."""
    prefix = model_artifact_prefix(sport, model_name)
    version = next_model_version(s3.list_keys(prefix))
    model_key = model_artifact_key(sport, model_name, version, artifact_filename)
    model_card_key = model_artifact_key(sport, model_name, version, MODEL_CARD_FILENAME)

    s3.put_bytes(model_key, model_bytes, content_type="application/octet-stream")
    logger.info("Wrote model artifact to s3://%s/%s", s3.bucket, model_key)

    model_card = {
        "sport": sport,
        "model_name": model_name,
        "algorithm": algorithm,
        "version": version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        **metadata,
    }
    s3.put_json(model_card_key, model_card)
    logger.info("Wrote model card to s3://%s/%s", s3.bucket, model_card_key)

    metric_summary = " ".join(f"{name}={metadata[name]:.4f}" for name in summary_metrics)
    logger.info(
        "Training complete: %s/%s v%d (%s) -- %s (%d train rows, %d test rows)",
        sport, model_name, version, algorithm, metric_summary, metadata["train_rows"], metadata["test_rows"],
    )
    return model_card


def get_current_version(s3: S3Manager, sport: str, model_name: str) -> int | None:
    """None means no version of this model has ever been promoted --
    distinct from "version 1 is current", which is why this can't just
    return 0 as a sentinel."""
    key = current_version_key(sport, model_name)
    if not s3.object_exists(key):
        return None
    return s3.get_json(key)["version"]


def promote_if_better(s3: S3Manager, sport: str, model_name: str, version: int, metadata: dict, metric: str) -> bool:
    """Points model_name's "current" pointer (current_version_key) at
    `version` unless an existing production version already scores
    meaningfully better on `metric` -- guards against a retrain with bad
    luck (an unlucky hyperparameter search, or a bug) silently becoming
    production just because it's the newest. A held-back version is
    still fully trained and versioned, never deleted -- only not
    promoted -- so it stays available for manual review or promotion.

    `metric` must name a lower-is-better value present on both model
    cards (log_loss for a classifier, rmse for a regressor) -- accuracy
    or anything else higher-is-better would invert this comparison.
    Algorithm-agnostic: `version`'s own algorithm doesn't have to match
    whatever algorithm the currently-promoted version happens to be --
    this only ever compares the two versions' own `metric` value, so a
    logistic-regression version can beat and replace an xgboost one, or
    vice versa, with no special-casing here (see
    library.ml.backtest.run_backtest, which is what actually runs several
    algorithms and calls this once on whichever one wins among them).

    Not a controlled A/B: the current production version's score was
    measured on an older, now-stale holdout window, while `version`'s was
    measured on a newer one that includes since-completed games. Good
    enough to catch a genuine regression, not precise enough to
    adjudicate between two versions within normal noise of each other --
    see PROMOTION_TOLERANCE.
    """
    current_version = get_current_version(s3, sport, model_name)
    if current_version is None:
        logger.info("No existing production version for %s/%s -- promoting v%d directly.", sport, model_name, version)
        s3.put_json(current_version_key(sport, model_name), {"version": version})
        return True

    current_card = s3.get_json(model_artifact_key(sport, model_name, current_version, MODEL_CARD_FILENAME))
    current_score = current_card[metric]
    new_score = metadata[metric]
    threshold = current_score * (1 + PROMOTION_TOLERANCE)

    if new_score <= threshold:
        logger.info(
            "Promoting %s/%s v%d (%s=%.4f) over current production v%d (%s=%.4f).",
            sport, model_name, version, metric, new_score, current_version, metric, current_score,
        )
        s3.put_json(current_version_key(sport, model_name), {"version": version})
        return True

    logger.warning(
        "Holding back %s/%s v%d (%s=%.4f) -- more than %.0f%% worse than current production v%d "
        "(%s=%.4f). Still versioned and available, just not promoted automatically.",
        sport, model_name, version, metric, new_score, PROMOTION_TOLERANCE * 100, current_version, metric, current_score,
    )
    return False
