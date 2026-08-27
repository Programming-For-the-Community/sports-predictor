"""
PGA individual match win-probability model training -- one row per
individual match (foursomes/fourball/singles from Ryder Cup/Presidents
Cup, or a bracket match from WGC-Dell Technologies Match Play), home-side
vs. away-side rolling stroke-play form as features (library/features/pga.
py's build_match_event_features), label_home_won as the binary target.

Same "run every CANDIDATES adapter as a competing candidate against the
same chronological holdout split, promote whichever wins on log_loss"
mechanics as train_top10_model.py -- a genuinely new label/feature set,
not a new task type, so no new code in library/ml/backtest.py or
library/ml/model_types.py.

Reads match_features.parquet (written by Source/feature-engineering/pga/
build_dataset.py) from S3.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_match_winprob_model.py
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
logger = logging.getLogger("pga-train-model")

SPORT = "pga"
MODEL_NAME = "match-win-probability"
MATCH_FEATURES_KEY = "pga/training-data/match_features.parquet"

# Identifiers/non-numeric context, never model inputs. match_format is a
# string category ("foursome"/"fourball"/"singles") -- is_singles already
# carries the one binary distinction that matters for feature averaging
# (a pairing's 2-golfer mean vs. a single golfer's own form), so
# match_format itself is excluded rather than left to numeric_frame's
# pd.to_numeric coercion (which would just turn it into a useless all-NaN
# column, not a real categorical signal).
NON_FEATURE_COLUMNS = {"event_key", "event_date", "match_format"}
LABEL_COLUMN = "label_home_won"
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
    result ({"promotions": [card, ...], "candidates": [summary, ...]}).

    A halved match has label_home_won=None (library/features/pga.py's
    build_match_event_features) -- dropped here before the split, same
    "filter at train time, keep the raw dataset complete" convention
    train_cutline_model.py's own cut_count > 0 filter uses."""
    df = df[df[LABEL_COLUMN].notna()]
    feature_columns = _feature_columns(df)
    train_df, test_df = training_common.chronological_split(df, training_common.TEST_FRACTION)
    train_date_range = [str(train_df["event_date"].min()), str(train_df["event_date"].max())]
    test_date_range = [str(test_df["event_date"].min()), str(test_df["event_date"].max())]
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), *train_date_range, len(test_df), *test_date_range,
    )

    X_train = training_common.numeric_frame(train_df, feature_columns)
    y_train = train_df[LABEL_COLUMN].astype(int)
    X_test = training_common.numeric_frame(test_df, feature_columns)
    y_test = test_df[LABEL_COLUMN].astype(int)

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

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, MATCH_FEATURES_KEY)
    df = training_common.load_features(s3, MATCH_FEATURES_KEY)
    logger.info("Loaded %d match rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
