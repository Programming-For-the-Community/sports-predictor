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

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

from library.ml import backtest, training_common
from library.ml import train_player_prop_model_common as player_prop_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nba-train-model")

SPORT = "nba"
PLAYER_FEATURES_KEY = "nba/training-data/player_features.parquet"

_job = player_prop_common.PlayerPropJob(globals(), SPORT, PLAYER_FEATURES_KEY)
NON_FEATURE_COLUMNS = player_prop_common.PLAYER_IDENTIFIER_COLUMNS
LABEL_COLUMN = player_prop_common.LABEL_COLUMN
MIN_PRIOR_GAMES_WITH_STAT = player_prop_common.MIN_PRIOR_GAMES_WITH_STAT
CANDIDATES = _job.candidates
_model_name = player_prop_common.model_name
_filter_to_target_stat = _job.filter_to_target_stat
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
