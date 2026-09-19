"""
F1 DNF (did-not-finish) probability model training -- genuinely new,
with no PGA analog at all (a missed cut/withdrawal removes a golfer from
FUTURE rounds of the same tournament, but never zeroes out an already-
recorded finish position for a round already played; an F1 DNF is a
binary "did this driver's OWN race end early" outcome with no other
sport's model quite matching it).

Reads driver_features.parquet (written by Source/feature-engineering/f1/
build_dataset.py) from S3, then runs every CANDIDATES adapter as a
competing candidate against the same chronological holdout split via
library.ml.backtest.run_backtest, and promotes whichever wins on
log_loss -- identical mechanics to train_winprob_model.py, just a
different label. label_dnf (see library/normalize/f1.py's map_status) is
1 specifically for an UNCLASSIFIED retirement, not any non-finish --
a classified-but-retired result (covered enough race distance) is 0
here, same real distinction library/features/f1.py's own
build_driver_event_features docstring explains.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_dnf_model.py
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
MODEL_NAME = "dnf-probability"
DRIVER_FEATURES_KEY = "f1/training-data/driver_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "entity_id", "constructor_entity_id", "event_date", "circuit_id"}
LABEL_COLUMN = "label_dnf"

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
