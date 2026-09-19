"""
F1 race-win-probability model training -- the "N=1" analog of PGA's own
top-10/top-5 ranking-style models (design/PROJECT_PLAN.md's F1
onboarding plan): a plain win/loss label is a genuinely rare positive
class in a 20-driver field (one winner per race), same underlying reason
PGA's own flagship model isn't a binary win classifier either, but a
single-winner field is common enough across sports (see every head-to-
head sport's own win-probability model) that this project's existing
binary-classification harness already handles it well -- no new task
type needed.

Reads driver_features.parquet (written by Source/feature-engineering/f1/
build_dataset.py) from S3, then runs every CANDIDATES adapter as a
competing candidate against the same chronological holdout split via
library.ml.backtest.run_backtest, and promotes whichever wins on
log_loss -- identical mechanics to PGA's train_top10_model.py.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_winprob_model.py
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
logger = logging.getLogger("f1-train-model")

SPORT = "f1"
MODEL_NAME = "win-probability"
DRIVER_FEATURES_KEY = "f1/training-data/driver_features.parquet"

# Identifiers/raw-string context, never model inputs -- constructor_
# entity_id and circuit_id are strings (pd.to_numeric would silently
# coerce them to an all-NaN column otherwise, see
# library.ml.training_common.numeric_frame's own docstring).
NON_FEATURE_COLUMNS = {"event_key", "entity_id", "constructor_entity_id", "event_date", "circuit_id"}
LABEL_COLUMN = "label_win"

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

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, DRIVER_FEATURES_KEY)
    df = training_common.load_features(s3, DRIVER_FEATURES_KEY)
    logger.info("Loaded %d driver-race rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
