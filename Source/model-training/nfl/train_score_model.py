"""
NFL game score model training -- one model per score target:
SCORE_TARGET=margin for the game's final margin (home score minus away
score), SCORE_TARGET=home_score or SCORE_TARGET=away_score for each
team's actual final score. Reads event_features.parquet and derives
whichever label SCORE_TARGET asks for from the label_home_score/
label_away_score columns at training time.

One script covers all three targets; SCORE_TARGET is the only thing that
varies. Run a given target by overriding the SCORE_TARGET environment
variable at `aws ecs run-task` time.

Runs every CANDIDATES adapter as a competing candidate against the same
holdout split via library.ml.backtest.run_backtest, and promotes
whichever wins on rmse.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    SCORE_TARGET (one of "margin", "home_score", "away_score")
    AWS_REGION

Usage:
    SCORE_TARGET=margin python train_score_model.py

Thin wrapper around library.ml.train_score_model_common (confirmed
byte-identical logic across nfl/nba/ncaafb/ncaambb before sharing there) --
only this sport's own feature-column exclusions and candidate algorithm
list stay here.
"""
import logging
import os

try:
    # Must run before any sklearn import (including the one directly below).
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

from library.aws.s3_manager import S3Manager
from library.ml import backtest, train_score_model_common as common, training_common
from library.ml.model_types import ElasticNetAdapter, MLPRegressorAdapter, RandomForestRegressorAdapter, XGBoostRegressorAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

SPORT = "nfl"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"

# Identifiers, never model inputs.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id", "venue_city", "venue_state"}
LABEL_COLUMN = common.LABEL_COLUMN
SUMMARY_METRICS = common.SUMMARY_METRICS
PROMOTION_METRIC = common.PROMOTION_METRIC

# Each target versions independently (nfl/score-margin/, nfl/home-score/, nfl/away-score/).
MODEL_NAMES = common.MODEL_NAMES

CANDIDATES = [
    XGBoostRegressorAdapter(),
    ElasticNetAdapter(),
    RandomForestRegressorAdapter(),
    MLPRegressorAdapter(),
]

_model_name = common.model_name
_add_label = common.add_label
_naive_prediction = common.naive_prediction


def _feature_columns(df):
    return training_common.feature_columns(df, NON_FEATURE_COLUMNS)


def train(s3: S3Manager, df, score_target: str) -> dict:
    return common.train(
        s3, df, score_target, sport=SPORT, candidates=CANDIDATES,
        non_feature_columns=NON_FEATURE_COLUMNS, logger=logger,
    )


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    score_target = os.environ["SCORE_TARGET"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)
    model_name = _model_name(score_target)

    logger.info("Loading %s training data from s3://%s/%s", model_name, bucket, EVENT_FEATURES_KEY)
    df = training_common.load_features(s3, EVENT_FEATURES_KEY)
    logger.info("Loaded %d event rows", len(df))

    train(s3, df, score_target)


if __name__ == "__main__":
    main()
