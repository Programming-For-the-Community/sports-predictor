"""
PGA top-5-finish-probability model training -- a near-identical sibling
of train_top10_model.py (same golfer_features.parquet dataset, same
5-candidate classifier tournament), just a stricter threshold. top-5 is a
genuinely rarer, harder-to-predict outcome than top-10 (roughly half as
many positive rows in any given tournament), which is exactly why it's
its own dedicated target rather than a derived rethreshold of the top-10
model's own output -- a golfer top-10-likely isn't necessarily top-5-
likely by the same margin, so this gets its own trained decision
boundary, not an assumption borrowed from a different target.

Shares its Docker image with train_top10_model.py (see this directory's
own Dockerfile) -- Terraform's ecs-task-pga-train-top5-model.tf points at
that same image tag with a command override, rather than a second image
build, since the two scripts have identical dependencies.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_top5_model.py
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
MODEL_NAME = "top-5-probability"
GOLFER_FEATURES_KEY = "pga/training-data/golfer_features.parquet"

# Identifiers, never model inputs.
NON_FEATURE_COLUMNS = {"event_key", "entity_id", "event_date"}
LABEL_COLUMN = "label_top_5"

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
