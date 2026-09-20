"""
NCAAFB player-prop model training. One model per target stat
(TARGET_STAT=passing_yards, rushing_yards, etc. -- see
Terraform/dynamodb-sport-registry.tf's ncaafb_player_prop_stats). Reads
player_features.parquet (written by
Source/feature-engineering/ncaafb/build_dataset.py's build_player_dataset)
from S3.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    TARGET_STAT (a stat_line key, e.g. "passing_yards", "defensive_sacks")
    AWS_REGION

Usage:
    TARGET_STAT=passing_yards python train_player_prop_model.py
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
from library.ml import train_player_prop_model_common as player_prop_common
from library.ml.model_types import ElasticNetAdapter, MLPRegressorAdapter, RandomForestRegressorAdapter, XGBoostRegressorAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaafb-train-model")

SPORT = "ncaafb"
PLAYER_FEATURES_KEY = "ncaafb/training-data/player_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "player_key", "entity_id", "team_id", "opponent_id", "event_date"}
LABEL_COLUMN = "label_target_stat"

CANDIDATES = [
    XGBoostRegressorAdapter(),
    ElasticNetAdapter(),
    RandomForestRegressorAdapter(),
    MLPRegressorAdapter(),
]

MIN_PRIOR_GAMES_WITH_STAT = 2
MIN_AVG_FRACTION_OF_MEDIAN = 0.35
MIN_NON_NULL_FRACTION = 0.05

# CFBD's box-score categories: passing, rushing, receiving, fumbles,
# defensive, kicking, punting. "fumbles"/"kicking"/"punting" are in
# neither set here, left to MIN_NON_NULL_FRACTION to decide their fate.
OFFENSIVE_CATEGORIES = {"passing", "rushing", "receiving"}
DEFENSIVE_CATEGORIES = {"defensive"}


_model_name = player_prop_common.model_name


def _filter_to_target_stat(df: pd.DataFrame, target_stat: str) -> pd.DataFrame:
    return player_prop_common.filter_to_target_stat(
        df, target_stat, label_column=LABEL_COLUMN,
        min_prior_games_with_stat=MIN_PRIOR_GAMES_WITH_STAT,
        min_avg_fraction_of_median=MIN_AVG_FRACTION_OF_MEDIAN,
    )


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
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
    df = _filter_to_target_stat(df, target_stat)
    return player_prop_common.train(
        s3, df, target_stat, sport=SPORT, candidates=CANDIDATES,
        label_column=LABEL_COLUMN, logger=logger,
        feature_columns_fn=lambda d: _feature_columns(d, target_stat),
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
