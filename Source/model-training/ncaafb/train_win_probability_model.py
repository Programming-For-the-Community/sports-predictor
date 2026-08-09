"""
NCAAFB win-probability model training -- the CFBD-sourced equivalent of
Source/model-training/nfl/train_win_probability_model.py. Reads
event_features.parquet (written by
Source/feature-engineering/ncaafb/build_dataset.py) from S3, then runs
the same multi-algorithm candidate tournament via
library.ml.backtest.run_backtest and promotes whichever wins on log_loss
-- see the NFL script's own docstring for the tournament mechanics, which
this mirrors exactly (same harness, same candidate roster, different
sport's data).

Scheduled -- see Terraform/ecs-task-ncaafb-train-win-probability-model.tf
and the registry-driven training orchestrator (sfn-training-orchestrator.tf,
dynamodb-sport-registry.tf's ncaafb_registry item) -- but every bit as
runnable manually via `aws ecs run-task`, same as
Source/feature-engineering/ncaafb.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_win_probability_model.py
"""
import logging
import os

try:
    # See NFL's own train_win_probability_model.py comment -- must run
    # before any sklearn import (including the one directly below).
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml.model_types import (
    LogisticRegressionAdapter,
    MLPClassifierAdapter,
    RandomForestClassifierAdapter,
    XGBoostClassifierAdapter,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaafb-train-model")

SPORT = "ncaafb"
MODEL_NAME = "win-probability"
EVENT_FEATURES_KEY = "ncaafb/training-data/event_features.parquet"

# Identifiers, never model inputs. Unlike NFL's own NON_FEATURE_COLUMNS,
# there's no venue_city/venue_state here -- library/features/ncaafb.py's
# build_event_features never surfaces raw venue strings as a column.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id"}
LABEL_COLUMN = "label_home_won"
SUMMARY_METRICS = ["accuracy", "log_loss", "naive_baseline_accuracy"]
PROMOTION_METRIC = "log_loss"

CANDIDATES = [
    XGBoostClassifierAdapter(),
    LogisticRegressionAdapter(),
    RandomForestClassifierAdapter(),
    MLPClassifierAdapter(),
]


def _feature_columns(df):
    return training_common.feature_columns(df, NON_FEATURE_COLUMNS)


def train(s3: S3Manager, df) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"winner": card, "promoted": bool, "candidates": [card, ...]})."""
    feature_columns = _feature_columns(df)
    train_df, test_df = training_common.chronological_split(df, training_common.TEST_FRACTION)
    train_date_range = [str(train_df["event_date"].min()), str(train_df["event_date"].max())]
    test_date_range = [str(test_df["event_date"].min()), str(test_df["event_date"].max())]
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), *train_date_range, len(test_df), *test_date_range,
    )

    X_train = training_common.numeric_frame(train_df, feature_columns)
    y_train = train_df[LABEL_COLUMN]
    X_test = training_common.numeric_frame(test_df, feature_columns)
    y_test = test_df[LABEL_COLUMN]

    naive_baseline_metrics = {"naive_baseline_accuracy": float(y_test.mean())}

    return backtest.run_backtest(
        s3, SPORT, MODEL_NAME, task="classification",
        X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
        candidates=CANDIDATES,
        naive_baseline_metrics=naive_baseline_metrics,
        extra_metadata={
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "train_date_range": train_date_range,
            "test_date_range": test_date_range,
        },
        summary_metrics=SUMMARY_METRICS,
        promotion_metric=PROMOTION_METRIC,
    )


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, EVENT_FEATURES_KEY)
    df = training_common.load_features(s3, EVENT_FEATURES_KEY)
    logger.info("Loaded %d event rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
