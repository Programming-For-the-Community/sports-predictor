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
MODEL_NAME = "cup-win-probability"
CUP_FEATURES_KEY = "pga/training-data/cup_features.parquet"

# tournament_name is context (always "Ryder Cup" or "Presidents Cup"
# today), never a model input -- deliberately not one-hot-encoded into a
# feature given how few rows exist per tournament name to learn a
# meaningful per-competition effect from.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "tournament_name"}
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
    result ({"promotions": [card, ...], "candidates": [summary, ...]}).

    A halved (tied) Cup has label_home_won=None (library/features/pga.py's
    build_cup_event_features) -- dropped before the split, same "filter at
    train time, keep the raw dataset complete" convention
    train_cutline_model.py's own cut_count > 0 filter uses."""
    return classifier_common.train(
        s3, df, SPORT, MODEL_NAME,
        label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS,
        candidates=CANDIDATES, logger=logger,
        drop_null_label=True, coerce_int_label=True,
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
