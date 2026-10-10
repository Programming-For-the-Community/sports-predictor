"""
NHL player-prop model training -- one model per target stat, run via the
TARGET_STAT environment variable at `aws ecs run-task` time.

Skater stats (shots_total, points, goals, assists, hits, blocked_shots)
read player_features.parquet; goalie stats (saves, goals_against) read
goalie_features.parquet, which holds one row per starting goalie. Both
are written by Source/feature-engineering/nhl/build_dataset.py.

Each model is a regressor predicting the stat's value, trained on rows
where the player has an established history of it (see
library.ml.train_player_prop_model_common.filter_to_target_stat).

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    TARGET_STAT (a stat_line key, e.g. "shots_total", "saves")
    AWS_REGION

Usage:
    TARGET_STAT=shots_total python train_player_prop_model.py
"""
import logging
import os

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml import train_player_prop_model_common as player_prop_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nhl-train-model")

SPORT = "nhl"
PLAYER_FEATURES_KEY = "nhl/training-data/player_features.parquet"
GOALIE_FEATURES_KEY = "nhl/training-data/goalie_features.parquet"

SKATER_STATS = frozenset({"shots_total", "points", "goals", "assists", "hits", "blocked_shots"})
GOALIE_STATS = frozenset({"saves", "goals_against"})

_skater_job = player_prop_common.PlayerPropJob(globals(), SPORT, PLAYER_FEATURES_KEY)
_goalie_job = player_prop_common.PlayerPropJob(globals(), SPORT, GOALIE_FEATURES_KEY)
NON_FEATURE_COLUMNS = player_prop_common.PLAYER_IDENTIFIER_COLUMNS
LABEL_COLUMN = player_prop_common.LABEL_COLUMN
CANDIDATES = _skater_job.candidates
_model_name = player_prop_common.model_name


def _job_for(target_stat: str) -> player_prop_common.PlayerPropJob:
    if target_stat in GOALIE_STATS:
        return _goalie_job
    if target_stat in SKATER_STATS:
        return _skater_job
    raise ValueError(f"Unknown TARGET_STAT: {target_stat!r} (expected one of {sorted(SKATER_STATS | GOALIE_STATS)})")


def _feature_columns(df, target_stat: str) -> list[str]:
    return _job_for(target_stat).feature_columns(df, target_stat)


def train(s3: S3Manager, df, target_stat: str) -> dict:
    return _job_for(target_stat).train(s3, df, target_stat)


def main() -> None:
    _job_for(os.environ["TARGET_STAT"]).main()


if __name__ == "__main__":
    main()
