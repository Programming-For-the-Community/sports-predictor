"""
NCAAFB National Ranking (1-25) model training, at team-week granularity
rather than event-level or player-level. Reads ranking_features.parquet
(written by Source/feature-engineering/ncaafb/build_dataset.py's
build_ranking_dataset) from S3, trains only on team-weeks CFBD's AP Top
25 poll actually ranked (label_current_rank not null), and runs the same
multi-algorithm candidate tournament via library.ml.backtest.run_backtest
as every other training script here.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_ranking_model.py
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
from library.ml import train_regressor_model_common as regressor_common
from library.ml.model_types import ElasticNetAdapter, MLPRegressorAdapter, RandomForestRegressorAdapter, XGBoostRegressorAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaafb-train-model")

SPORT = "ncaafb"
MODEL_NAME = "national-ranking"
RANKING_FEATURES_KEY = "ncaafb/training-data/ranking_features.parquet"

# Identifiers/non-numeric columns, never model inputs. "season" is
# excluded since an absolute year doesn't generalize as a feature; "week"
# stays in since it's numeric and captures season progress.
NON_FEATURE_COLUMNS = {"event_key", "team_id", "event_date", "season", "season_type", "conference"}
LABEL_COLUMN = "label_current_rank"

CANDIDATES = [
    XGBoostRegressorAdapter(),
    ElasticNetAdapter(),
    RandomForestRegressorAdapter(),
    MLPRegressorAdapter(),
]


def _filter_to_ranked_weeks(df: pd.DataFrame) -> pd.DataFrame:
    return df[df[LABEL_COLUMN].notna()].copy()


def _feature_columns(df: pd.DataFrame) -> list[str]:
    return training_common.feature_columns(df, NON_FEATURE_COLUMNS)


def train(s3: S3Manager, df: pd.DataFrame) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
    df = _filter_to_ranked_weeks(df)
    return regressor_common.train(
        s3, df, SPORT, MODEL_NAME,
        label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS,
        candidates=CANDIDATES, logger=logger,
    )


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, RANKING_FEATURES_KEY)
    df = training_common.load_features(s3, RANKING_FEATURES_KEY)
    logger.info("Loaded %d team-week rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
