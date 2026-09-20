"""
NFL win-probability model training. Reads event_features.parquet (written
by Source/feature-engineering/nfl/build_dataset.py) from S3, then runs
XGBoost, logistic regression, Random Forest, and an MLP as competing
candidates against the same chronological holdout split via
library.ml.backtest.run_backtest, and promotes whichever wins on
log_loss. Every retrain re-runs the full tournament, so the promoted
algorithm can change automatically as more data comes in.

Scheduled -- see Terraform/scheduler-nfl-train-win-probability-model.tf --
but also runnable manually via `aws ecs run-task`.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_win_probability_model.py
"""
import logging
import os

try:
    # Patches scikit-learn's LogisticRegression/ElasticNet/RandomForest with
    # Intel oneDAL-accelerated implementations -- must run before
    # library.ml.model_types (or anything else importing sklearn) so the
    # patched classes are what actually get instantiated. XGBoost has its
    # own native optimization and isn't affected either way.
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
logger = logging.getLogger("nfl-train-model")

SPORT = "nfl"
MODEL_NAME = "win-probability"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"

# Identifiers, never model inputs. Every other label_* column is excluded
# generically below; this target only predicts label_home_won.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id", "venue_city", "venue_state"}
LABEL_COLUMN = "label_home_won"

# Four classification-capable candidates: XGBoost, logistic regression,
# Random Forest, and a small MLP.
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
