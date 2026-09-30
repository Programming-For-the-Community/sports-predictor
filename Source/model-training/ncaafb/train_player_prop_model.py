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

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

from library.ml import backtest, training_common
from library.ml import train_player_prop_model_common as player_prop_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaafb-train-model")

SPORT = "ncaafb"
PLAYER_FEATURES_KEY = "ncaafb/training-data/player_features.parquet"

_job = player_prop_common.PlayerPropJob(
    globals(), SPORT, PLAYER_FEATURES_KEY, include_lightgbm=False,
    offensive_categories=frozenset({"passing", "rushing", "receiving"}),
    defensive_categories=frozenset({"defensive"}),
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
