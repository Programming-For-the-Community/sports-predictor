"""
Shared plumbing for every NFL model-training script -- train_model.py
(the production XGBoost classifier) and train_baseline_model.py (a
logistic-regression comparison baseline), plus any future model type.
Loading the training set, splitting it chronologically, coercing feature
dtypes, computing holdout metrics, and writing a versioned model artifact
plus model card are identical regardless of which estimator is being
trained -- only the estimator itself, its hyperparameter search, and how
its weights get serialized are model-specific and stay in each script.
"""
import io
import logging
from datetime import datetime, timezone

import pandas as pd
from sklearn.metrics import accuracy_score, log_loss

from library.aws.s3_manager import S3Manager
from library.storage.model_artifacts import model_artifact_key, model_artifact_prefix, next_model_version

logger = logging.getLogger("nfl-train-model")

SPORT = "nfl"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"

# Identifiers, never model inputs. Every other label_* column is excluded
# generically below -- every model here only predicts label_home_won, the
# score columns belong to a future score-margin model, not this one.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id", "venue_city", "venue_state"}
LABEL_COLUMN = "label_home_won"

# Fraction of events (most recent, by date) held out for evaluation.
# Chronological, not random -- a random split would leak future games
# into training in a way that doesn't match how this model actually gets
# used: predicting games that haven't happened yet from ones that have.
TEST_FRACTION = 0.2


def load_features(s3: S3Manager) -> pd.DataFrame:
    data = s3.get_bytes(EVENT_FEATURES_KEY)
    return pd.read_parquet(io.BytesIO(data))


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [col for col in df.columns if col not in NON_FEATURE_COLUMNS and not col.startswith("label_")]


def chronological_split(df: pd.DataFrame, test_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = df.sort_values("event_date")
    split_index = int(len(ordered) * (1 - test_fraction))
    return ordered.iloc[:split_index], ordered.iloc[split_index:]


def numeric_frame(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """A feature column that's entirely null in the training window (e.g.
    weather_temperature, which real ESPN history frequently doesn't
    report) has no non-null value for pandas to infer a numeric dtype
    from, so it comes back from Parquet as dtype `object` rather than
    float64 -- and both XGBoost and scikit-learn reject `object` columns
    outright, even when every value is just a missing float. Coercing
    explicitly (bools included) guarantees every feature column is
    numeric before it ever reaches a model, regardless of how sparse any
    single column is."""
    return df[columns].apply(pd.to_numeric, errors="coerce")


def evaluate_holdout(model, X_test: pd.DataFrame, y_test: pd.Series) -> dict:
    predictions = model.predict(X_test)
    probabilities = model.predict_proba(X_test)[:, 1]
    return {
        # float() casts -- sklearn returns numpy scalar types, which
        # json.dumps() (via S3Manager.put_json) doesn't know how to
        # serialize, the same lesson learned from DynamoDB's Decimal.
        "accuracy": float(accuracy_score(y_test, predictions)),
        "log_loss": float(log_loss(y_test, probabilities)),
    }


def save_model_artifact(
    s3: S3Manager, model_name: str, algorithm: str, model_bytes: bytes, artifact_filename: str, metadata: dict,
) -> dict:
    """Writes a versioned model artifact plus its model card -- everything
    needed to know what a given version is (training window, row counts,
    hyperparameters, holdout metrics) without re-running anything or
    cross-referencing CloudWatch Logs. See library.storage.model_artifacts
    for the versioning scheme -- each model_name (e.g. "win-probability",
    "win-probability-logistic") versions independently under its own
    prefix, so a baseline run never collides with or skips the production
    model's version numbers."""
    prefix = model_artifact_prefix(SPORT, model_name)
    version = next_model_version(s3.list_keys(prefix))
    model_key = model_artifact_key(SPORT, model_name, version, artifact_filename)
    model_card_key = model_artifact_key(SPORT, model_name, version, "model_card.json")

    s3.put_bytes(model_key, model_bytes, content_type="application/octet-stream")
    logger.info("Wrote model artifact to s3://%s/%s", s3.bucket, model_key)

    model_card = {
        "sport": SPORT,
        "model_name": model_name,
        "algorithm": algorithm,
        "version": version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        **metadata,
    }
    s3.put_json(model_card_key, model_card)
    logger.info("Wrote model card to s3://%s/%s", s3.bucket, model_card_key)

    # One line with the version and its score together -- there's no
    # backtesting harness yet (design/PROJECT_PLAN.md Phase 4), so this is
    # the fastest way to eyeball "did this run look reasonable" straight
    # from CloudWatch Logs without opening the model card.
    logger.info(
        "Training complete: %s v%d (%s) -- accuracy=%.4f log_loss=%.4f (%d train rows, %d test rows)",
        model_name, version, algorithm, metadata["accuracy"], metadata["log_loss"],
        metadata["train_rows"], metadata["test_rows"],
    )
    return model_card
