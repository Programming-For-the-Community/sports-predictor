"""
NFL player-prop model training -- one model per target stat (e.g.
TARGET_STAT=passing_yards for QB passing yards, TARGET_STAT=rushing_yards
for RB rushing yards, TARGET_STAT=receptions for a receiver). Reads
player_features.parquet (written by
Source/feature-engineering/nfl/build_dataset.py's build_player_dataset)
from S3, filters to rows where the player actually recorded TARGET_STAT
in the game being labeled AND has an established history of it (see
_filter_to_target_stat), and trains a regressor predicting that value
from their own rolling stat history (see build_player_features and
rolling_player_stat_averages in library/features/nfl.py).

One script covers every stat; TARGET_STAT is the only thing that varies.
Run a given stat by overriding the TARGET_STAT environment variable at
`aws ecs run-task` time.

Runs every CANDIDATES adapter as a competing candidate against the same
holdout split via library.ml.backtest.run_backtest, and promotes
whichever wins on rmse.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    TARGET_STAT (a stat_line key, e.g. "passing_yards", "rushing_yards")
    AWS_REGION

Usage:
    TARGET_STAT=passing_yards python train_player_prop_model.py
"""
import logging

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

from library.ml import backtest, training_common
from library.ml import train_player_prop_model_common as player_prop_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

SPORT = "nfl"
PLAYER_FEATURES_KEY = "nfl/training-data/player_features.parquet"

_job = player_prop_common.PlayerPropJob(
    globals(), SPORT, PLAYER_FEATURES_KEY, include_lightgbm=False,
    offensive_categories=frozenset({"passing", "rushing", "receiving"}),
    defensive_categories=frozenset({"defensive", "interceptions"}),
)
NON_FEATURE_COLUMNS = player_prop_common.PLAYER_IDENTIFIER_COLUMNS
LABEL_COLUMN = player_prop_common.LABEL_COLUMN
MIN_PRIOR_GAMES_WITH_STAT = player_prop_common.MIN_PRIOR_GAMES_WITH_STAT
CANDIDATES = _job.candidates
_model_name = player_prop_common.model_name
_filter_to_target_stat = _job.filter_to_target_stat
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main
OFFENSIVE_CATEGORIES = _job.offensive_categories
DEFENSIVE_CATEGORIES = _job.defensive_categories
_stat_category = _job.stat_category
_opposing_side_categories = _job.opposing_side_categories


if __name__ == "__main__":
    main()
