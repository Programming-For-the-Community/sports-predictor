"""
PGA Cup (team) win-probability model training -- one row per Ryder Cup/
Presidents Cup, home-team vs. away-team rolling stroke-play form averaged
across each team's FULL roster as features (library/features/pga.py's
build_cup_event_features), label_home_won as the binary target.

Same "run every CANDIDATES adapter as a competing candidate against the
same chronological holdout split, promote whichever wins on log_loss"
mechanics as train_top10_model.py/train_match_winprob_model.py -- a
genuinely new label/feature set, not a new task type.

Reads cup_features.parquet (written by Source/feature-engineering/pga/
build_dataset.py) from S3. This is the SMALLEST dataset of the 6 PGA
models by far -- only Ryder Cup/Presidents Cup editions since 2017 (WGC
Match Play has no Cup-level row at all, see library/normalize/
pga_matchplay.py's own module docstring) -- so treat a promoted model's
metrics with proportionally more skepticism than the other 5's.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_cup_winprob_model.py
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
MODEL_NAME = "cup-win-probability"
CUP_FEATURES_KEY = "pga/training-data/cup_features.parquet"

# tournament_name is context (always "Ryder Cup" or "Presidents Cup"
# today), never a model input -- deliberately not one-hot-encoded into a
# feature given how few rows exist per tournament name to learn a
# meaningful per-competition effect from.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "tournament_name"}
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

    A halved (tied) Cup has label_home_won=None (library/features/pga.py's
    build_cup_event_features) -- dropped here before the split, same
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

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, CUP_FEATURES_KEY)
    df = training_common.load_features(s3, CUP_FEATURES_KEY)
    logger.info("Loaded %d cup rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
