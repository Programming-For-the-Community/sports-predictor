"""
PGA Cup (team) win-probability model training -- one row per Ryder Cup/
Presidents Cup, home-team vs. away-team rolling stroke-play form averaged
across each team's FULL roster as features (library/features/pga.py's
build_cup_event_features), label_home_won as the binary target.

Same "run every CANDIDATES adapter as a competing candidate against the
same chronological holdout split, promote whichever wins on log_loss"
mechanics as train_top10_model.py/train_match_winprob_model.py -- a
genuinely new label/feature set, not a new task type.

Reads cup_features.parquet (written by Source/feature-engineering/pga/
build_dataset.py) from S3. This is the SMALLEST dataset of the 6 PGA
models by far -- only Ryder Cup/Presidents Cup editions since 2017 (WGC
Match Play has no Cup-level row at all, see library/normalize/
pga_matchplay.py's own module docstring) -- so treat a promoted model's
metrics with proportionally more skepticism than the other 5's.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_cup_winprob_model.py
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
MODEL_NAME = "cup-win-probability"
CUP_FEATURES_KEY = "pga/training-data/cup_features.parquet"

# tournament_name is context (always "Ryder Cup" or "Presidents Cup"
# today), never a model input -- deliberately not one-hot-encoded into a
# feature given how few rows exist per tournament name to learn a
# meaningful per-competition effect from.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "tournament_name"}
LABEL_COLUMN = "label_home_won"

CANDIDATES = model_types.classifier_candidates()

_job = training_common.ModelJob(
    globals(),
    trainer=classifier_common.train, sport=SPORT, model_name=MODEL_NAME, features_key=CUP_FEATURES_KEY,
    row_noun="cup", label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS, candidates=CANDIDATES,
    drop_null_label=True, coerce_int_label=True,
)
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
