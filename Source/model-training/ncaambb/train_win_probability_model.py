"""
NCAA MBB win-probability model training. Reads event_features.parquet
(written by Source/feature-engineering/ncaambb/build_dataset.py) from S3,
then runs every CANDIDATES adapter as a competing candidate against the
same chronological holdout split via library.ml.backtest.run_backtest,
and promotes whichever wins on log_loss. See library/ml/backtest.py for
the tournament mechanics and library/ml/model_types.py for what each
candidate actually is.

Scheduled -- see the training orchestrator (sfn-training-orchestrator.tf,
driven by dynamodb-sport-registry.tf's ncaambb_registry item) -- but also
runnable manually via `aws ecs run-task`.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_win_probability_model.py
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
logger = logging.getLogger("ncaambb-train-model")

SPORT = "ncaambb"
MODEL_NAME = "win-probability"
EVENT_FEATURES_KEY = "ncaambb/training-data/event_features.parquet"

# Identifiers, never model inputs.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id"}
LABEL_COLUMN = "label_home_won"

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
        # Always predict the home team wins, no model -- accuracy is the
        # fraction of home wins in the holdout set.
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
