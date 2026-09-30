"""
PGA individual match win-probability model training -- one row per
individual match (foursomes/fourball/singles from Ryder Cup/Presidents
Cup, or a bracket match from WGC-Dell Technologies Match Play), home-side
vs. away-side rolling stroke-play form as features (library/features/pga.
py's build_match_event_features), label_home_won as the binary target.

Same "run every CANDIDATES adapter as a competing candidate against the
same chronological holdout split, promote whichever wins on log_loss"
mechanics as train_top10_model.py -- a genuinely new label/feature set,
not a new task type, so no new code in library/ml/backtest.py or
library/ml/model_types.py.

Reads match_features.parquet (written by Source/feature-engineering/pga/
build_dataset.py) from S3.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_match_winprob_model.py
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
MODEL_NAME = "match-win-probability"
MATCH_FEATURES_KEY = "pga/training-data/match_features.parquet"

# Identifiers/non-numeric context, never model inputs. match_format is a
# string category ("foursome"/"fourball"/"singles") -- is_singles already
# carries the one binary distinction that matters for feature averaging
# (a pairing's 2-golfer mean vs. a single golfer's own form), so
# match_format itself is excluded rather than left to numeric_frame's
# pd.to_numeric coercion (which would just turn it into a useless all-NaN
# column, not a real categorical signal).
NON_FEATURE_COLUMNS = {"event_key", "event_date", "match_format"}
LABEL_COLUMN = "label_home_won"

CANDIDATES = model_types.classifier_candidates()

_job = training_common.ModelJob(
    globals(),
    trainer=classifier_common.train, sport=SPORT, model_name=MODEL_NAME, features_key=MATCH_FEATURES_KEY,
    row_noun="match", label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS, candidates=CANDIDATES,
    drop_null_label=True, coerce_int_label=True,
)
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
