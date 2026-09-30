"""
F1 podium (top-3 finish) probability model training -- a less extreme
positive-class rarity than win-probability's (3 podium spots vs. 1
winner per race), the PGA analog being train_top10_model.py/
train_top5_model.py's own "top-N finish" pattern generalized to F1's own
smaller, differently-shaped field.

Reads driver_features.parquet (written by Source/feature-engineering/f1/
build_dataset.py) from S3, then runs every CANDIDATES adapter as a
competing candidate against the same chronological holdout split via
library.ml.backtest.run_backtest, and promotes whichever wins on
log_loss -- identical mechanics to train_winprob_model.py, just a
different label.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_podium_model.py
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
MODEL_NAME = "podium-probability"
DRIVER_FEATURES_KEY = "f1/training-data/driver_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "entity_id", "constructor_entity_id", "event_date", "circuit_id"}
LABEL_COLUMN = "label_podium"

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
