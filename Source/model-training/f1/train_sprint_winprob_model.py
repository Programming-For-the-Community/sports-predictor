"""
F1 Sprint-race-win-probability model training -- the Sprint-race analog
of train_winprob_model.py, trained on its own separate rolling history
(see library/normalize/f1.py's sprint_result_to_event_item docstring for
why Sprint isn't blended into main-race form).

Reads sprint_features.parquet (written by Source/feature-engineering/f1/
build_dataset.py) -- by far the smallest of every F1 dataset (Sprint
format only exists 2021+, and only a handful of rounds per season are
Sprint weekends even within that window), so treat a promoted model's
metrics with proportionally more skepticism than the other F1 models',
same caution PGA's own train_cup_winprob_model.py flags for its own
smallest dataset. Runs every CANDIDATES adapter as a competing candidate
against the same chronological holdout split via
library.ml.backtest.run_backtest, and promotes whichever wins on
log_loss -- identical mechanics to train_winprob_model.py.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_sprint_winprob_model.py
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
MODEL_NAME = "sprint-win-probability"
SPRINT_FEATURES_KEY = "f1/training-data/sprint_features.parquet"

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

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, SPRINT_FEATURES_KEY)
    df = training_common.load_features(s3, SPRINT_FEATURES_KEY)
    logger.info("Loaded %d driver-Sprint-race rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
