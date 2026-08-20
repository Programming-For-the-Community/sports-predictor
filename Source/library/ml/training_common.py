"""
Shared plumbing for every sport's model-training scripts -- loading a
training set, splitting it chronologically, coercing feature dtypes,
computing holdout metrics, and writing a versioned model artifact plus
model card are identical regardless of sport, algorithm, or which
dataset/label a script targets. Every function below takes `sport`
explicitly, so a future sport's training scripts import this exact
module rather than reaching across a sport boundary or copy-pasting it.

evaluate_holdout/evaluate_regression_holdout take already-computed
predictions/probabilities rather than a model object -- library.ml.
model_types.ModelAdapter.predict() is what produces those, uniformly
across every algorithm.
"""
import io
import logging
import os
import time
import uuid
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
    run_progress_key,
)

logger = logging.getLogger("model-training")

LOAD_FEATURES_MAX_ATTEMPTS = 3
LOAD_FEATURES_BACKOFF_SECONDS = 2.0

MODEL_CARD_FILENAME = "model_card.json"

# Fraction of events (most recent, by date) held out for evaluation.
# Chronological, not random -- a random split would leak future games
# into training in a way that doesn't match how this model actually gets
# used: predicting games that haven't happened yet from ones that have.
TEST_FRACTION = 0.2


def load_features(s3: S3Manager, key: str) -> pd.DataFrame:
    """Retries on a truncated/corrupt read (pyarrow.ArrowInvalid) rather
    than failing the whole training run over one bad S3 GET.

    Imports pyarrow locally rather than at module level -- it's not one
    of library's own declared dependencies, only its training-script
    callers', and a module-level import would make every caller of this
    module need it installed just to import training_common at all."""
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
    """A feature column that's entirely null in the training window has no
    non-null value for pandas to infer a numeric dtype from, so it comes
    back from Parquet as dtype `object` rather than float64 -- and both
    XGBoost and scikit-learn reject `object` columns outright. Coercing
    explicitly (bools included) guarantees every feature column is
    numeric before it ever reaches a model."""
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
    accuracy/log_loss don't apply. mae is carried on the model card
    alongside rmse since it's easier to read in the target's own units
    without RMSE's squared-error weighting toward big misses."""
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
    hyperparameters, holdout metrics) without re-running anything.
    Each (sport, model_name) pair versions independently under its own
    prefix, so different algorithms trained for the same target simply
    share that target's one version counter.

    summary_metrics: which metadata keys (e.g. ["accuracy", "log_loss"]
    or ["rmse", "mae"]) to fold into the one-line CloudWatch summary
    below."""
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


def update_promoted_candidates(
    s3: S3Manager, sport: str, model_name: str, version: int, candidates: list[dict], candidates_ranked_by: str,
) -> None:
    """Rewrites an already-saved model card's own `candidates`/
    `candidates_ranked_by` fields in place. Called once, at the very end
    of library.ml.backtest.run_backtest, on whichever version that run
    ends up leaving live -- run_backtest promotes a winning candidate the
    moment it beats current production, which can be before the rest of
    that run's candidates have even been tried, so the card written at
    promotion time only carries a partial "candidates" summary. This
    fills it in with the complete tournament once the whole run is known.

    Every other field on the card is left untouched."""
    key = model_artifact_key(sport, model_name, version, MODEL_CARD_FILENAME)
    card = s3.get_json(key)
    card["candidates"] = candidates
    card["candidates_ranked_by"] = candidates_ranked_by
    s3.put_json(key, card)
    logger.info(
        "Updated %s/%s v%d's model card with the full %d-candidate run summary.",
        sport, model_name, version, len(candidates),
    )


def would_beat_current(s3: S3Manager, sport: str, model_name: str, metadata: dict, metric: str) -> bool:
    """Read-only counterpart to promote_if_better's own comparison --
    answers "would this candidate get promoted" without writing anything,
    so a caller can decide whether a candidate is even worth persisting
    to S3 before calling save_model_artifact. Same rule as
    promote_if_better: True if there's no current production version yet,
    or metadata[metric] is genuinely at least as good as the current
    production version's own metric value."""
    current_version = get_current_version(s3, sport, model_name)
    if current_version is None:
        return True
    current_card = s3.get_json(model_artifact_key(sport, model_name, current_version, MODEL_CARD_FILENAME))
    return metadata[metric] <= current_card[metric]


def resolve_run_id() -> str:
    """Every train_*.py script's own run identity, threaded through to
    library.ml.backtest.run_backtest as its resumable-progress breadcrumb
    key. TRAINING_RUN_ID is set from the Step Functions execution's own
    name, stable across a retry attempt and its fallback, so a task that
    gets interrupted and relaunched resumes the same run instead of
    starting a fresh one. Falls back to a fresh uuid4 for a manual/local
    run outside the orchestrator."""
    return os.environ.get("TRAINING_RUN_ID") or str(uuid.uuid4())


def load_run_progress(s3: S3Manager, sport: str, model_name: str, run_id: str) -> dict | None:
    """None means this run_id has no breadcrumb yet for this model --
    either this is the first attempt, or a previous attempt already
    finished every candidate and cleared it (see clear_run_progress)."""
    key = run_progress_key(sport, model_name, run_id)
    if not s3.object_exists(key):
        return None
    return s3.get_json(key)


def save_run_progress(
    s3: S3Manager, sport: str, model_name: str, run_id: str,
    evaluated: list[dict], promotions: list[dict],
) -> None:
    """Overwrites the whole breadcrumb after every candidate (win or
    lose) -- small enough (a handful of candidates' metrics/cards) that a
    full overwrite each time is simpler than an append-only log, and
    idempotent if the same candidate's write happens to run twice."""
    s3.put_json(run_progress_key(sport, model_name, run_id), {
        "evaluated": evaluated,
        "promotions": promotions,
    })


def clear_run_progress(s3: S3Manager, sport: str, model_name: str, run_id: str) -> None:
    """Deletes the breadcrumb once library.ml.backtest.run_backtest
    finishes every candidate normally -- nothing left to resume, and
    leaving it around would just be a stale S3 object from here on."""
    s3.delete_object(run_progress_key(sport, model_name, run_id))


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
    `version` unless an existing production version already scores better
    on `metric` -- guards against a retrain with bad luck silently
    becoming production just because it's the newest. A held-back
    version is still fully trained and versioned, never deleted -- only
    not promoted -- so it stays available for manual review or promotion.

    Requires genuine improvement (new_score <= current_score, no
    percentage slack).

    `metric` must name a lower-is-better value present on both model
    cards (log_loss for a classifier, rmse for a regressor) -- accuracy
    or anything else higher-is-better would invert this comparison.
    Algorithm-agnostic: `version`'s own algorithm doesn't have to match
    whatever algorithm the currently-promoted version happens to be --
    this only ever compares the two versions' own `metric` value.

    Not a controlled A/B: the current production version's score was
    measured on an older, now-stale holdout window, while `version`'s was
    measured on a newer one that includes since-completed games -- so a
    "win" here is against a moving target, not a perfectly fair rematch.
    """
    current_version = get_current_version(s3, sport, model_name)
    if current_version is None:
        logger.info("No existing production version for %s/%s -- promoting v%d directly.", sport, model_name, version)
        s3.put_json(current_version_key(sport, model_name), {"version": version})
        return True

    current_card = s3.get_json(model_artifact_key(sport, model_name, current_version, MODEL_CARD_FILENAME))
    current_score = current_card[metric]
    new_score = metadata[metric]

    if new_score <= current_score:
        logger.info(
            "Promoting %s/%s v%d (%s=%.4f) over current production v%d (%s=%.4f).",
            sport, model_name, version, metric, new_score, current_version, metric, current_score,
        )
        s3.put_json(current_version_key(sport, model_name), {"version": version})
        return True

    logger.info(
        "Holding back %s/%s v%d (%s=%.4f) -- current production v%d (%s=%.4f) is still better. "
        "Still versioned and available, just not promoted automatically.",
        sport, model_name, version, metric, new_score, current_version, metric, current_score,
    )
    return False
