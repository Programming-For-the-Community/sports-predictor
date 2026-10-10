"""
NHL game score model training -- one model per score target:
SCORE_TARGET=margin for the game's final margin (home score minus away
score), SCORE_TARGET=home_score or SCORE_TARGET=away_score for each
team's final score. Reads event_features.parquet and derives whichever
label SCORE_TARGET asks for from label_home_score/label_away_score.

Those labels are ESPN's final score, the one shown and graded, which
credits a shootout winner with one extra goal -- so the margin is never
zero.

One script covers all three targets; run a given target via the
SCORE_TARGET environment variable at `aws ecs run-task` time.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    SCORE_TARGET (one of "margin", "home_score", "away_score")
    AWS_REGION

Usage:
    SCORE_TARGET=margin python train_score_model.py
"""
import logging

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

from library.aws.s3_manager import S3Manager
from library.features.nhl import WINDOWS
from library.ml import backtest, train_score_model_common as common, training_common

import nhl_training

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nhl-train-model")

SPORT = "nhl"
EVENT_FEATURES_KEY = nhl_training.EVENT_FEATURES_KEY

# The shared naive baseline reads each side's rolling scoring averages
# under these names; hockey's are its shortest-window goal averages.
NAIVE_BASELINE_SOURCES = {
    f"{side}_avg_points_{shared}": f"{side}_goals_{hockey}_last{WINDOWS[0]}"
    for side in ("home", "away") for shared, hockey in (("scored", "for"), ("allowed", "against"))
}

_job = common.ScoreJob(
    globals(), SPORT, EVENT_FEATURES_KEY,
    extra_non_feature_columns=nhl_training.EXTRA_NON_FEATURE_COLUMNS | frozenset(NAIVE_BASELINE_SOURCES),
)
NON_FEATURE_COLUMNS = _job.non_feature_columns
LABEL_COLUMN = common.LABEL_COLUMN
CANDIDATES = _job.candidates
_model_name = common.model_name
_feature_columns = _job.feature_columns
main = _job.main


def train(s3: S3Manager, df, score_target: str) -> dict:
    baseline_columns = {alias: df[source] for alias, source in NAIVE_BASELINE_SOURCES.items()}
    return _job.train(s3, df.assign(**baseline_columns), score_target)


if __name__ == "__main__":
    main()
