"""
PGA top-10-finish-probability model training -- the "ranking-style model"
Phase 5 step 3 calls for, deliberately NOT the win/loss binary classifier
every head-to-head sport's flagship model is (see design/PROJECT_PLAN.md's
Phase 5 checklist). A plain win/loss label barely exists for a 100+
entrant field (one winner out of the whole field, an extremely rare
positive class), where "will this golfer finish in the top 10" is both a
genuinely rankable outcome and a real target this project's existing
binary-classification training harness already handles well -- no new
task type needed in library/ml/backtest.py or library/ml/model_types.py.

Reads golfer_features.parquet (written by Source/feature-engineering/pga/
build_dataset.py) from S3, then runs every CANDIDATES adapter as a
competing candidate against the same chronological holdout split via
library.ml.backtest.run_backtest, and promotes whichever wins on
log_loss -- identical mechanics to every other sport's win-probability
model, just a different label and feature set.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_top10_model.py
"""
import logging

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

from library.ml import backtest, model_types, training_common
from library.ml import train_classifier_model_common as classifier_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pga-train-model")

SPORT = "pga"
MODEL_NAME = "top-10-probability"
GOLFER_FEATURES_KEY = "pga/training-data/golfer_features.parquet"

# Identifiers, never model inputs.
NON_FEATURE_COLUMNS = {"event_key", "entity_id", "event_date"}
LABEL_COLUMN = "label_top_10"

CANDIDATES = model_types.classifier_candidates()

_job = training_common.ModelJob(
    globals(),
    trainer=classifier_common.train, sport=SPORT, model_name=MODEL_NAME, features_key=GOLFER_FEATURES_KEY,
    row_noun="golfer-tournament", label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS,
    candidates=CANDIDATES,
)
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
