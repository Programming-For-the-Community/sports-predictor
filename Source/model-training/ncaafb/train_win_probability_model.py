"""
NCAAFB win-probability model training. Reads event_features.parquet
(written by Source/feature-engineering/ncaafb/build_dataset.py) from S3,
then runs a multi-algorithm candidate tournament via
library.ml.backtest.run_backtest and promotes whichever wins on log_loss.

Scheduled -- see Terraform/ecs-task-ncaafb-train-win-probability-model.tf
and the registry-driven training orchestrator (sfn-training-orchestrator.tf,
dynamodb-sport-registry.tf's ncaafb_registry item) -- but also runnable
manually via `aws ecs run-task`.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_win_probability_model.py
"""
import logging
import os

try:
    # Must run before any sklearn import (including the one directly below).
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml import train_classifier_model_common as classifier_common
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

# Identifiers, never model inputs.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id"}
LABEL_COLUMN = "label_home_won"

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
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
    return classifier_common.train(
        s3, df, SPORT, MODEL_NAME,
        label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS,
        candidates=CANDIDATES, logger=logger,
        naive_baseline_fn=lambda y_test: float(y_test.mean()),
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
