"""
F1 projected-Sprint-starting-grid model training -- a continuous
regression target, the closest available "Sprint qualifying" model this
project can build. label_sprint_grid_position is a driver's real
starting grid for the Sprint race itself (see library/features/f1.py's
build_sprint_event_features docstring) -- Jolpica has no separate Sprint
Qualifying/Sprint Shootout results endpoint at all, so there is no
lap-time/pace data behind this target the
way train_qualifying_model.py's own gap-to-pole-derived features have
for the main qualifying session; this model can only learn from rolling
Sprint-specific race-day form (see sprint_features.parquet's own
feature set), not from any real practice-pace signal for that session.

Reads sprint_features.parquet (written by Source/feature-engineering/f1/
build_dataset.py) -- by far the smallest of every F1 dataset (Sprint
format only exists 2021+, and only a handful of rounds per season are
Sprint weekends even within that window), so treat a promoted model's
metrics with proportionally more skepticism than the other F1 models',
same caution PGA's own train_cup_winprob_model.py flags for its own
smallest dataset.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_sprint_grid_model.py
"""
import logging

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

import pandas as pd

from library.ml import backtest, model_types, training_common
from library.ml import train_regressor_model_common as regressor_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("f1-train-model")

SPORT = "f1"
MODEL_NAME = "projected-sprint-grid-position"
SPRINT_FEATURES_KEY = "f1/training-data/sprint_features.parquet"

NON_FEATURE_COLUMNS = {"event_key", "entity_id", "constructor_entity_id", "event_date", "circuit_id"}
LABEL_COLUMN = "label_sprint_grid_position"

CANDIDATES = model_types.regressor_candidates()


def _filter_to_scored_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df[df[LABEL_COLUMN].notna()].copy()


_job = training_common.ModelJob(
    globals(),
    trainer=regressor_common.train, sport=SPORT, model_name=MODEL_NAME, features_key=SPRINT_FEATURES_KEY,
    row_noun="driver-Sprint-race", label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS,
    candidates=CANDIDATES, prepare=_filter_to_scored_rows,
)
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
