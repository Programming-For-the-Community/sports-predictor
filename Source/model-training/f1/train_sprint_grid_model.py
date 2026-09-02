"""
F1 projected-Sprint-starting-grid model training -- a continuous
regression target, the closest available "Sprint qualifying" model this
project can build. label_sprint_grid_position is a driver's real
starting grid for the Sprint race itself (see library/features/f1.py's
build_sprint_event_features docstring) -- Jolpica has no separate Sprint
Qualifying/Sprint Shootout results endpoint at all, so there is no
lap-time/pace data behind this target the
way train_qualifying_model.py's own gap-to-pole-derived features have
for the main qualifying session; this model can only learn from rolling
Sprint-specific race-day form (see sprint_features.parquet's own
feature set), not from any real practice-pace signal for that session.

Reads sprint_features.parquet (written by Source/feature-engineering/f1/
build_dataset.py) -- by far the smallest of every F1 dataset (Sprint
format only exists 2021+, and only a handful of rounds per season are
Sprint weekends even within that window), so treat a promoted model's
metrics with proportionally more skepticism than the other F1 models',
same caution PGA's own train_cup_winprob_model.py flags for its own
smallest dataset.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_sprint_grid_model.py
"""
import logging
import os

try:
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
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
MODEL_NAME = "projected-sprint-grid-position"
SPRINT_FEATURES_KEY = "f1/training-data/sprint_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "entity_id", "constructor_entity_id", "event_date", "circuit_id"}
LABEL_COLUMN = "label_sprint_grid_position"
SUMMARY_METRICS = ["rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae"]
PROMOTION_METRIC = "rmse"

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

    median_position = y_train.median()
    naive_predictions = pd.Series(median_position, index=y_test.index)
    naive_baseline_metrics = {
        "naive_baseline_rmse": float(root_mean_squared_error(y_test, naive_predictions)),
        "naive_baseline_mae": float(mean_absolute_error(y_test, naive_predictions)),
    }

    return backtest.run_backtest(
        s3, SPORT, MODEL_NAME, task="regression",
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

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, SPRINT_FEATURES_KEY)
    df = training_common.load_features(s3, SPRINT_FEATURES_KEY)
    logger.info("Loaded %d driver-Sprint-race rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
