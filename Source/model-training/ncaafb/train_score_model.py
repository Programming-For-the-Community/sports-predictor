"""
NCAAFB game score model training. One model per score target:
SCORE_TARGET=margin/home_score/away_score, reading the same
event_features.parquet as train_win_probability_model.py.

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
logger = logging.getLogger("ncaafb-train-model")

SPORT = "ncaafb"
EVENT_FEATURES_KEY = "ncaafb/training-data/event_features.parquet"

_job = common.ScoreJob(globals(), SPORT, EVENT_FEATURES_KEY, include_lightgbm=False)
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
