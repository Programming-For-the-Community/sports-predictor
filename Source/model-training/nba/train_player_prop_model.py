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
import logging
import os

try:
    # Must run before any sklearn import (including the one directly below).
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

import pandas as pd

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml import train_player_prop_model_common as player_prop_common
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


_model_name = player_prop_common.model_name


def _filter_to_target_stat(df: pd.DataFrame, target_stat: str) -> pd.DataFrame:
    return player_prop_common.filter_to_target_stat(
        df, target_stat, label_column=LABEL_COLUMN,
        min_prior_games_with_stat=MIN_PRIOR_GAMES_WITH_STAT,
        min_avg_fraction_of_median=MIN_AVG_FRACTION_OF_MEDIAN,
    )


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
    return player_prop_common.train(
        s3, df, target_stat, sport=SPORT, candidates=CANDIDATES,
        label_column=LABEL_COLUMN, logger=logger, feature_columns_fn=_feature_columns,
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
