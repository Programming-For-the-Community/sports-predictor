"""
PGA top-10-finish-probability model training -- the "ranking-style model"
Phase 5 step 3 calls for, deliberately NOT the win/loss binary classifier
every head-to-head sport's flagship model is (see design/PROJECT_PLAN.md's
Phase 5 checklist). A plain win/loss label barely exists for a 100+
entrant field (one winner out of the whole field, an extremely rare
positive class), where "will this golfer finish in the top 10" is both a
genuinely rankable outcome and a real target this project's existing
binary-classification training harness already handles well -- no new
task type needed in library/ml/backtest.py or library/ml/model_types.py.

Reads golfer_features.parquet (written by Source/feature-engineering/pga/
build_dataset.py) from S3, then runs every CANDIDATES adapter as a
competing candidate against the same chronological holdout split via
library.ml.backtest.run_backtest, and promotes whichever wins on
log_loss -- identical mechanics to every other sport's win-probability
model, just a different label and feature set.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_top10_model.py
"""
import logging
import os

try:
    # Must run before any sklearn import (including the one directly
    # below). XGBoost and LightGBM both have their own native
    # optimization and aren't affected either way.
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml import train_classifier_model_common as classifier_common
from library.ml.model_types import (
    LightGBMClassifierAdapter,
    LogisticRegressionAdapter,
    MLPClassifierAdapter,
    RandomForestClassifierAdapter,
    XGBoostClassifierAdapter,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pga-train-model")

SPORT = "pga"
MODEL_NAME = "top-10-probability"
GOLFER_FEATURES_KEY = "pga/training-data/golfer_features.parquet"

# Identifiers, never model inputs.
NON_FEATURE_COLUMNS = {"event_key", "entity_id", "event_date"}
LABEL_COLUMN = "label_top_10"

CANDIDATES = [
    XGBoostClassifierAdapter(),
    LogisticRegressionAdapter(),
    RandomForestClassifierAdapter(),
    MLPClassifierAdapter(),
    LightGBMClassifierAdapter(),
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
    )


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, GOLFER_FEATURES_KEY)
    df = training_common.load_features(s3, GOLFER_FEATURES_KEY)
    logger.info("Loaded %d golfer-tournament rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
