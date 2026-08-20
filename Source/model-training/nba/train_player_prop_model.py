"""
NBA player-prop model training -- one model per target stat (e.g.
TARGET_STAT=points, TARGET_STAT=rebounds). Reads player_features.parquet
(written by Source/feature-engineering/nba/build_dataset.py's
build_player_dataset) from S3, filters to rows where the player actually
recorded TARGET_STAT in the game being labeled AND has an established
history of it (see _filter_to_target_stat), and trains a regressor
predicting that value from their own rolling stat history (see
build_player_features and rolling_player_stat_averages in
library/features/nba.py and library/features/common.py). Run a given
stat via the TARGET_STAT environment variable at `aws ecs run-task` time.

Every NBA player's stat_line carries the same flat key set (points,
rebounds, assists, steals, blocks, turnovers,
field_goals_made/field_goal_attempts, etc.) regardless of position, so
MIN_NON_NULL_FRACTION alone is sufficient in _feature_columns -- there's
no side-of-the-ball exclusion to apply.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    TARGET_STAT (a stat_line key, e.g. "points", "rebounds")
    AWS_REGION

Usage:
    TARGET_STAT=points python train_player_prop_model.py
"""
import json
import logging
import os

try:
    # Must run before any sklearn import (including the one directly below).
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
logger = logging.getLogger("nba-train-model")

SPORT = "nba"
PLAYER_FEATURES_KEY = "nba/training-data/player_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "player_key", "entity_id", "team_id", "opponent_id", "event_date"}
LABEL_COLUMN = "label_target_stat"
SUMMARY_METRICS = ["rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae"]
PROMOTION_METRIC = "rmse"

CANDIDATES = [
    XGBoostRegressorAdapter(),
    ElasticNetAdapter(),
    RandomForestRegressorAdapter(),
    MLPRegressorAdapter(),
    LightGBMRegressorAdapter(),
]

# A player must have recorded TARGET_STAT in at least this many of their
# own windowed prior games, not just the game being labeled -- guards
# against a one-off garbage-time stat line whose rolling avg_<stat> would
# otherwise be undefined/NaN.
MIN_PRIOR_GAMES_WITH_STAT = 2

# Excludes a bench player who clears the games_with_<stat> bar above at
# trivial volume (e.g. 2 minutes a night) rather than one-off.
MIN_AVG_FRACTION_OF_MEDIAN = 0.35

# A column surviving an all-null check with even one real value isn't enough.
MIN_NON_NULL_FRACTION = 0.05


def _model_name(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def _filter_to_target_stat(df: pd.DataFrame, target_stat: str) -> pd.DataFrame:
    """Filters to rows where the player recorded target_stat at
    meaningful volume. label_stat_line is JSON-encoded, so it's parsed
    here before filtering on it or turning it into a label; also applies
    the MIN_PRIOR_GAMES_WITH_STAT and MIN_AVG_FRACTION_OF_MEDIAN
    thresholds."""
    stat_lines = df["label_stat_line"].apply(json.loads)
    has_stat = stat_lines.apply(lambda stat_line: target_stat in stat_line)
    filtered = df[has_stat].copy()
    filtered[LABEL_COLUMN] = stat_lines[has_stat].apply(lambda stat_line: float(stat_line[target_stat]))

    volume_column = f"games_with_{target_stat}"
    filtered = filtered[filtered[volume_column] >= MIN_PRIOR_GAMES_WITH_STAT]

    avg_column = f"avg_{target_stat}"
    median_avg = filtered[avg_column].median()
    filtered = filtered[filtered[avg_column] >= median_avg * MIN_AVG_FRACTION_OF_MEDIAN]

    return filtered


def _feature_columns(df: pd.DataFrame) -> list[str]:
    """MIN_NON_NULL_FRACTION alone is the filter; NBA's flat stat_line
    has no side-of-the-ball distinction to exclude."""
    candidates = training_common.feature_columns(df, NON_FEATURE_COLUMNS)
    minimum_non_null = len(df) * MIN_NON_NULL_FRACTION
    return [col for col in candidates if df[col].notna().sum() >= minimum_non_null]


def train(s3: S3Manager, df: pd.DataFrame, target_stat: str) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
    df = _filter_to_target_stat(df, target_stat)
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

    # A trivial baseline: predict this player's own rolling average
    # directly, no model.
    naive_predictions = test_df[f"avg_{target_stat}"]
    naive_baseline_metrics = {
        "naive_baseline_rmse": float(root_mean_squared_error(y_test, naive_predictions)),
        "naive_baseline_mae": float(mean_absolute_error(y_test, naive_predictions)),
    }

    return backtest.run_backtest(
        s3, SPORT, _model_name(target_stat), task="regression",
        X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
        candidates=CANDIDATES,
        naive_baseline_metrics=naive_baseline_metrics,
        extra_metadata={
            "target_stat": target_stat,
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
    target_stat = os.environ["TARGET_STAT"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)
    model_name = _model_name(target_stat)

    logger.info("Loading %s training data from s3://%s/%s", model_name, bucket, PLAYER_FEATURES_KEY)
    df = training_common.load_features(s3, PLAYER_FEATURES_KEY)
    logger.info("Loaded %d player-game rows", len(df))

    train(s3, df, target_stat)


if __name__ == "__main__":
    main()
