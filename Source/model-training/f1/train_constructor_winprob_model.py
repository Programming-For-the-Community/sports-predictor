"""
F1 constructor (team) race-win-probability model training -- one row per
constructor per race, both of that constructor's drivers' rolling form
SUMMED (not averaged, see library/features/f1.py's build_constructor_
event_features/_sum_forms docstrings for why) as features, label_win as
the binary target (1 if EITHER of the constructor's drivers won).

Same "run every CANDIDATES adapter as a competing candidate against the
same chronological holdout split, promote whichever wins on log_loss"
mechanics as train_winprob_model.py -- the PGA analog named in this
project's own F1 onboarding plan is train_cup_winprob_model.py's
team-aggregate pattern, though PGA's team aggregate is an AVERAGE across
a full Ryder Cup roster, not a 2-driver SUM -- see the feature-module
docstrings above for why F1's own real points-are-a-sum rule makes sum
the correct aggregation here instead.

Reads constructor_features.parquet (written by Source/feature-
engineering/f1/build_dataset.py) from S3 -- a genuinely different,
smaller dataset than the other 4 F1 models' driver_features.parquet.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_constructor_winprob_model.py
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
MODEL_NAME = "constructor-win-probability"
CONSTRUCTOR_FEATURES_KEY = "f1/training-data/constructor_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "entity_id", "event_date", "circuit_id"}
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

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, CONSTRUCTOR_FEATURES_KEY)
    df = training_common.load_features(s3, CONSTRUCTOR_FEATURES_KEY)
    logger.info("Loaded %d constructor-race rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
