"""
NCAA MBB game score model training -- one model per score target:
SCORE_TARGET=margin for the game's final margin (home score minus away
score), SCORE_TARGET=home_score or SCORE_TARGET=away_score for each
team's actual final score. Reads event_features.parquet and derives
whichever label SCORE_TARGET asks for from the label_home_score/
label_away_score columns at training time.

One script covers all three targets; run a given target via the
SCORE_TARGET environment variable at `aws ecs run-task` time.

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

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

from library.ml import backtest, train_score_model_common as common, training_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaambb-train-model")

SPORT = "ncaambb"
EVENT_FEATURES_KEY = "ncaambb/training-data/event_features.parquet"

_job = common.ScoreJob(globals(), SPORT, EVENT_FEATURES_KEY)
NON_FEATURE_COLUMNS = _job.non_feature_columns
LABEL_COLUMN = common.LABEL_COLUMN
CANDIDATES = _job.candidates
_model_name = common.model_name
_add_label = common.add_label
_naive_prediction = common.naive_prediction
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
