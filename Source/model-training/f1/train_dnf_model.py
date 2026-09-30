"""
F1 DNF (did-not-finish) probability model training -- genuinely new,
with no PGA analog at all (a missed cut/withdrawal removes a golfer from
FUTURE rounds of the same tournament, but never zeroes out an already-
recorded finish position for a round already played; an F1 DNF is a
binary "did this driver's OWN race end early" outcome with no other
sport's model quite matching it).

Reads driver_features.parquet (written by Source/feature-engineering/f1/
build_dataset.py) from S3, then runs every CANDIDATES adapter as a
competing candidate against the same chronological holdout split via
library.ml.backtest.run_backtest, and promotes whichever wins on
log_loss -- identical mechanics to train_winprob_model.py, just a
different label. label_dnf (see library/normalize/f1.py's map_status) is
1 specifically for an UNCLASSIFIED retirement, not any non-finish --
a classified-but-retired result (covered enough race distance) is 0
here, same real distinction library/features/f1.py's own
build_driver_event_features docstring explains.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_dnf_model.py
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
MODEL_NAME = "dnf-probability"
DRIVER_FEATURES_KEY = "f1/training-data/driver_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "entity_id", "constructor_entity_id", "event_date", "circuit_id"}
LABEL_COLUMN = "label_dnf"

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
