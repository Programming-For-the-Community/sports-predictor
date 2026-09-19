"""
F1 projected-qualifying-position model training -- a continuous
regression target, the direct qualifying-session analog of
train_finish_position_model.py's own race-day target.

label_qualifying_position is the REAL qualifying-session classification
position (from Jolpica's own qualifying.json, merged onto each race
event by library/normalize/f1.py's merge_qualifying_into_event) -- None
(excluded from training) for any row qualifying hasn't been merged into
yet, same filter-before-training discipline train_finish_position_model.py
already uses for a non-classified race result.

Reads driver_features.parquet (written by Source/feature-engineering/f1/
build_dataset.py) from S3 -- the SAME dataset every other driver-grain F1
model reads, not a separate qualifying-only dataset; qualifying is just
another label/feature block on the same per-(driver, race) row (see
library/features/f1.py's build_driver_event_features). Runs every
CANDIDATES adapter as a competing candidate against the same
chronological holdout split via library.ml.backtest.run_backtest, and
promotes whichever wins on rmse.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_qualifying_model.py
"""
import logging
import os

try:
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

import pandas as pd

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml import train_regressor_model_common as regressor_common
from library.ml.model_types import (
    ElasticNetAdapter,
    LightGBMRegressorAdapter,
    MLPRegressorAdapter,
    RandomForestRegressorAdapter,
    XGBoostRegressorAdapter,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("f1-train-model")

SPORT = "f1"
MODEL_NAME = "projected-qualifying-position"
DRIVER_FEATURES_KEY = "f1/training-data/driver_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "entity_id", "constructor_entity_id", "event_date", "circuit_id"}
LABEL_COLUMN = "label_qualifying_position"

CANDIDATES = [
    XGBoostRegressorAdapter(),
    ElasticNetAdapter(),
    RandomForestRegressorAdapter(),
    MLPRegressorAdapter(),
    LightGBMRegressorAdapter(),
]


def _filter_to_scored_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df[df[LABEL_COLUMN].notna()].copy()


def _feature_columns(df: pd.DataFrame) -> list[str]:
    return training_common.feature_columns(df, NON_FEATURE_COLUMNS)


def train(s3: S3Manager, df: pd.DataFrame) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
    df = _filter_to_scored_rows(df)
    return regressor_common.train(
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
