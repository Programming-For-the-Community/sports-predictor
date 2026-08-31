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
SUMMARY_METRICS = ["accuracy", "log_loss", "naive_baseline_accuracy"]
PROMOTION_METRIC = "log_loss"

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

    # A trivial baseline: always guess the majority class (almost always
    # "did not win" -- 1 winning constructor out of ~10 per race, so the
    # positive class here is a small minority). max() picks whichever
    # class is actually the majority in this holdout rather than
    # assuming it's always the negative one.
    naive_baseline_accuracy = float(max(y_test.mean(), 1 - y_test.mean()))
    naive_baseline_metrics = {"naive_baseline_accuracy": naive_baseline_accuracy}

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
        run_id=training_common.resolve_run_id(),
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
