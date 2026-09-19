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
    build_match_event_features) -- dropped before the split, same "filter
    at train time, keep the raw dataset complete" convention
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

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, MATCH_FEATURES_KEY)
    df = training_common.load_features(s3, MATCH_FEATURES_KEY)
    logger.info("Loaded %d match rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
