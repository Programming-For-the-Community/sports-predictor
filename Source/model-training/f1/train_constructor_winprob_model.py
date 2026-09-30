"""
F1 constructor (team) race-win-probability model training -- one row per
constructor per race, both of that constructor's drivers' rolling form
SUMMED (not averaged, see library/features/f1.py's build_constructor_
event_features/_sum_forms docstrings for why) as features, label_win as
the binary target (1 if EITHER of the constructor's drivers won).

Same "run every CANDIDATES adapter as a competing candidate against the
same chronological holdout split, promote whichever wins on log_loss"
mechanics as train_winprob_model.py -- the PGA analog named in this
project's own F1 onboarding plan is train_cup_winprob_model.py's
team-aggregate pattern, though PGA's team aggregate is an AVERAGE across
a full Ryder Cup roster, not a 2-driver SUM -- see the feature-module
docstrings above for why F1's own real points-are-a-sum rule makes sum
the correct aggregation here instead.

Reads constructor_features.parquet (written by Source/feature-
engineering/f1/build_dataset.py) from S3 -- a genuinely different,
smaller dataset than the other 4 F1 models' driver_features.parquet.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_constructor_winprob_model.py
"""
import logging

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

from library.ml import backtest, model_types, training_common
from library.ml import train_classifier_model_common as classifier_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("f1-train-model")

SPORT = "f1"
MODEL_NAME = "constructor-win-probability"
CONSTRUCTOR_FEATURES_KEY = "f1/training-data/constructor_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "entity_id", "event_date", "circuit_id"}
LABEL_COLUMN = "label_win"

CANDIDATES = model_types.classifier_candidates()

_job = training_common.ModelJob(
    globals(),
    trainer=classifier_common.train, sport=SPORT, model_name=MODEL_NAME, features_key=CONSTRUCTOR_FEATURES_KEY,
    row_noun="constructor-race", label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS,
    candidates=CANDIDATES,
)
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
