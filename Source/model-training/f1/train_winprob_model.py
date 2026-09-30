"""
F1 race-win-probability model training -- the "N=1" analog of PGA's own
top-10/top-5 ranking-style models (design/PROJECT_PLAN.md's F1
onboarding plan): a plain win/loss label is a genuinely rare positive
class in a 20-driver field (one winner per race), same underlying reason
PGA's own flagship model isn't a binary win classifier either, but a
single-winner field is common enough across sports (see every head-to-
head sport's own win-probability model) that this project's existing
binary-classification harness already handles it well -- no new task
type needed.

Reads driver_features.parquet (written by Source/feature-engineering/f1/
build_dataset.py) from S3, then runs every CANDIDATES adapter as a
competing candidate against the same chronological holdout split via
library.ml.backtest.run_backtest, and promotes whichever wins on
log_loss -- identical mechanics to PGA's train_top10_model.py.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_winprob_model.py
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
MODEL_NAME = "win-probability"
DRIVER_FEATURES_KEY = "f1/training-data/driver_features.parquet"

# Identifiers/raw-string context, never model inputs -- constructor_
# entity_id and circuit_id are strings (pd.to_numeric would silently
# coerce them to an all-NaN column otherwise, see
# library.ml.training_common.numeric_frame's own docstring).
NON_FEATURE_COLUMNS = {"event_key", "entity_id", "constructor_entity_id", "event_date", "circuit_id"}
LABEL_COLUMN = "label_win"

CANDIDATES = model_types.classifier_candidates()

_job = training_common.ModelJob(
    globals(),
    trainer=classifier_common.train, sport=SPORT, model_name=MODEL_NAME, features_key=DRIVER_FEATURES_KEY,
    row_noun="driver-race", label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS, candidates=CANDIDATES,
)
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
