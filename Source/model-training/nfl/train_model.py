"""
NFL win-probability model training. Reads event_features.parquet (written
by Source/feature-engineering/nfl/build_dataset.py) from S3, trains an
XGBoost binary classifier predicting label_home_won, and writes a
versioned model artifact plus a small metadata file to S3.

Not scheduled -- run manually via `aws ecs run-task`, same as
Source/feature-engineering/nfl (see Terraform/ecs-task-nfl-train-model.tf).
Each run trains a brand-new version under its own prefix; it never
overwrites a previous one -- see library.storage.model_artifacts for the
versioning scheme, and Terraform/s3-model-artifacts.tf for why versioning
is per-model rather than one shared counter across every model this sport
has.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_model.py
"""
import io
import logging
import os
from datetime import datetime, timezone

import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, log_loss

from library.aws.s3_manager import S3Manager
from library.storage.model_artifacts import model_artifact_key, model_artifact_prefix, next_model_version

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

SPORT = "nfl"
MODEL_NAME = "win-probability"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"

# Identifiers, never model inputs. Every other label_* column is excluded
# generically below (this model only predicts label_home_won -- the score
# columns belong to a future score-margin model, not this one).
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id"}
LABEL_COLUMN = "label_home_won"

# Fraction of events (most recent, by date) held out for evaluation.
# Chronological, not random -- a random split would leak future games
# into training in a way that doesn't match how this model actually gets
# used: predicting games that haven't happened yet from ones that have.
TEST_FRACTION = 0.2


def _load_features(s3: S3Manager) -> pd.DataFrame:
    data = s3.get_bytes(EVENT_FEATURES_KEY)
    return pd.read_parquet(io.BytesIO(data))


def _feature_columns(df: pd.DataFrame) -> list[str]:
    return [col for col in df.columns if col not in NON_FEATURE_COLUMNS and not col.startswith("label_")]


def _chronological_split(df: pd.DataFrame, test_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = df.sort_values("event_date")
    split_index = int(len(ordered) * (1 - test_fraction))
    return ordered.iloc[:split_index], ordered.iloc[split_index:]


def train(df: pd.DataFrame) -> tuple[xgb.XGBClassifier, dict]:
    """Trains on the older slice, evaluates on the most recent slice. XGBoost
    handles the NaN values early-season rows carry (no rolling history yet)
    natively -- no imputation needed."""
    feature_columns = _feature_columns(df)
    train_df, test_df = _chronological_split(df, TEST_FRACTION)
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), train_df["event_date"].min(), train_df["event_date"].max(),
        len(test_df), test_df["event_date"].min(), test_df["event_date"].max(),
    )

    X_train, y_train = train_df[feature_columns], train_df[LABEL_COLUMN]
    X_test, y_test = test_df[feature_columns], test_df[LABEL_COLUMN]

    model = xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss")
    model.fit(X_train, y_train)

    predictions = model.predict(X_test)
    probabilities = model.predict_proba(X_test)[:, 1]
    metrics = {
        # float()/int() casts -- sklearn returns numpy scalar types, which
        # json.dumps() (via S3Manager.put_json) doesn't know how to
        # serialize, the same lesson learned from DynamoDB's Decimal.
        "accuracy": float(accuracy_score(y_test, predictions)),
        "log_loss": float(log_loss(y_test, probabilities)),
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
    }
    logger.info("Holdout accuracy=%.4f log_loss=%.4f", metrics["accuracy"], metrics["log_loss"])

    return model, {"feature_columns": feature_columns, **metrics}


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, EVENT_FEATURES_KEY)
    df = _load_features(s3)
    logger.info("Loaded %d event rows", len(df))

    model, metadata = train(df)

    prefix = model_artifact_prefix(SPORT, MODEL_NAME)
    version = next_model_version(s3.list_keys(prefix))
    model_key = model_artifact_key(SPORT, MODEL_NAME, version, "model.xgb")
    metadata_key = model_artifact_key(SPORT, MODEL_NAME, version, "metadata.json")

    model_bytes = model.get_booster().save_raw()
    s3.put_bytes(model_key, model_bytes, content_type="application/octet-stream")
    logger.info("Wrote model artifact to s3://%s/%s", bucket, model_key)

    metadata_payload = {
        "sport": SPORT,
        "model_name": MODEL_NAME,
        "version": version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        **metadata,
    }
    s3.put_json(metadata_key, metadata_payload)
    logger.info("Wrote metadata to s3://%s/%s", bucket, metadata_key)

    # One line with the version and its score together -- there's no
    # backtesting harness yet (design/PROJECT_PLAN.md Phase 4), so this is
    # the fastest way to eyeball "did this week's retrain look reasonable"
    # straight from CloudWatch Logs without opening metadata.json.
    logger.info(
        "Training complete: %s v%d -- accuracy=%.4f log_loss=%.4f (%d train rows, %d test rows)",
        MODEL_NAME, version, metadata["accuracy"], metadata["log_loss"],
        metadata["train_rows"], metadata["test_rows"],
    )


if __name__ == "__main__":
    main()
