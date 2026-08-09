"""
NCAAFB player-prop model training -- the CFBD-sourced equivalent of
Source/model-training/nfl/train_player_prop_model.py. One model per
target stat (TARGET_STAT=passing_yards, rushing_yards, etc. -- the same
7 stats as NFL, see Terraform/dynamodb-sport-registry.tf's
ncaafb_player_prop_stats). Reads player_features.parquet (written by
Source/feature-engineering/ncaafb/build_dataset.py's build_player_dataset)
from S3 -- see the NFL script's own docstring for the filtering/training
mechanics, which this mirrors exactly.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    TARGET_STAT (a stat_line key, e.g. "passing_yards", "defensive_sacks")
    AWS_REGION

Usage:
    TARGET_STAT=passing_yards python train_player_prop_model.py
"""
import json
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
from library.ml.model_types import ElasticNetAdapter, MLPRegressorAdapter, RandomForestRegressorAdapter, XGBoostRegressorAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaafb-train-model")

SPORT = "ncaafb"
PLAYER_FEATURES_KEY = "ncaafb/training-data/player_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "player_key", "entity_id", "team_id", "opponent_id", "event_date"}
LABEL_COLUMN = "label_target_stat"
SUMMARY_METRICS = ["rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae"]
PROMOTION_METRIC = "rmse"

CANDIDATES = [
    XGBoostRegressorAdapter(),
    ElasticNetAdapter(),
    RandomForestRegressorAdapter(),
    MLPRegressorAdapter(),
]

MIN_PRIOR_GAMES_WITH_STAT = 2
MIN_AVG_FRACTION_OF_MEDIAN = 0.35
MIN_NON_NULL_FRACTION = 0.05

# CFBD's own box-score category names (confirmed live: passing, rushing,
# receiving, fumbles, defensive, kicking, punting -- no separate bare
# "interceptions" category the way ESPN/NFL has one, so DEFENSIVE_
# CATEGORIES here is just {"defensive"}, not the two-entry set NFL's own
# script needs). "fumbles"/"kicking"/"punting" are deliberately in
# neither set, same reasoning as NFL's own docstring -- left to
# MIN_NON_NULL_FRACTION to decide their fate.
OFFENSIVE_CATEGORIES = {"passing", "rushing", "receiving"}
DEFENSIVE_CATEGORIES = {"defensive"}


def _model_name(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def _filter_to_target_stat(df: pd.DataFrame, target_stat: str) -> pd.DataFrame:
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


def _stat_category(stat_key: str) -> str | None:
    for category in OFFENSIVE_CATEGORIES | DEFENSIVE_CATEGORIES:
        if stat_key == category or stat_key.startswith(f"{category}_"):
            return category
    return None


def _opposing_side_categories(target_stat: str) -> set[str]:
    target_category = _stat_category(target_stat)
    if target_category in OFFENSIVE_CATEGORIES:
        return DEFENSIVE_CATEGORIES
    if target_category in DEFENSIVE_CATEGORIES:
        return OFFENSIVE_CATEGORIES
    return set()


def _strip_metric_prefix(column: str) -> str:
    if column.startswith("avg_"):
        return column.removeprefix("avg_")
    if column.startswith("games_with_"):
        return column.removeprefix("games_with_")
    return column


def _feature_columns(df: pd.DataFrame, target_stat: str) -> list[str]:
    candidates = training_common.feature_columns(df, NON_FEATURE_COLUMNS)
    minimum_non_null = len(df) * MIN_NON_NULL_FRACTION
    candidates = [col for col in candidates if df[col].notna().sum() >= minimum_non_null]

    opposing_categories = _opposing_side_categories(target_stat)
    return [col for col in candidates if _stat_category(_strip_metric_prefix(col)) not in opposing_categories]


def train(s3: S3Manager, df: pd.DataFrame, target_stat: str) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"winner": card, "promoted": bool, "candidates": [card, ...]})."""
    df = _filter_to_target_stat(df, target_stat)
    feature_columns = _feature_columns(df, target_stat)
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
