"""
PGA projected-score-to-par model training -- a continuous regression
target, the basis for "field finish order": at serving time, a
tournament's whole field gets ranked by this model's own predictions,
lowest-predicted-score first. No separately trained "field order"
artifact and no rank-loss/learning-to-rank objective in this project's
shared training harness (library/ml/backtest.py), so this reuses the
existing regression infrastructure unchanged, same reasoning that kept
top-10/top-5 as plain binary classifiers.

The trained target is REMAINING score-to-par (label_remaining_score_to_par
-- strokes relative to par over whatever rounds aren't reflected in
score_to_par_this_week_so_far yet), not the absolute final score. See
library/features/pga.py's build_golfer_event_features docstring for why:
feeding the model its own current cumulative as a raw feature let that
feature dominate, collapsing predictions to roughly "final ≈ current
cumulative." Serving time (aws-lambdas/pga/predict/event_prediction.py)
adds this model's remaining-score output back onto the golfer's real
score_to_par_this_week_so_far to get the field-facing
projected_score_to_par value.

Reads golfer_features.parquet (the same dataset train_top10_model.py/
train_top5_model.py read) from S3, filters out rows with no recorded
final score (label_remaining_score_to_par.isna() -- a withdrawal before
playing a single hole), then runs the same 4-candidate regressor
tournament every other sport's score/ranking model uses via
library.ml.backtest.run_backtest, promoted on rmse.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_score_model.py
"""
import logging

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

import pandas as pd

from library.ml import backtest, model_types, training_common
from library.ml import train_regressor_model_common as regressor_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pga-train-model")

SPORT = "pga"
MODEL_NAME = "projected-score-to-par"
GOLFER_FEATURES_KEY = "pga/training-data/golfer_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "entity_id", "event_date"}
LABEL_COLUMN = "label_remaining_score_to_par"

CANDIDATES = model_types.regressor_candidates(include_lightgbm=False)


def _filter_to_scored_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df[df[LABEL_COLUMN].notna()].copy()


_job = training_common.ModelJob(
    globals(),
    trainer=regressor_common.train, sport=SPORT, model_name=MODEL_NAME, features_key=GOLFER_FEATURES_KEY,
    row_noun="golfer-tournament", label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS,
    candidates=CANDIDATES, prepare=_filter_to_scored_rows,
)
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
