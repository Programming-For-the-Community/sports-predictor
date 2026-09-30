"""
PGA projected-cut-line model training -- predicts a tournament's own
cut_score (the relative-to-par score that made the cut, e.g. -2) BEFORE
it's played, from tournament-level context alone (purse, is_major,
field_size, this course's own recent cut-score history). Genuinely
different grain from every other PGA model here: one row per TOURNAMENT,
not per golfer -- a cut line is a property of the whole field, not any
one golfer's own result (see feature-engineering/pga/build_dataset.py's
build_cutline_dataset and library/features/pga.py's build_cutline_event_
features).

Reads cutline_features.parquet from S3, filters to tournaments that
actually HAD a cut (cut_count > 0 -- a no-cut FedEx Cup playoff event
genuinely reports cut_count=0, not a missing value, see design/
DATA_SCHEMA.md), same "filter at train time, keep the raw dataset
complete" convention NCAAFB's own national-ranking model uses for its
own not-every-row-is-ranked case. Same 4-candidate regressor tournament
every other sport's score/ranking model uses, promoted on rmse.

"If a player gets cut we don't need to project their 3rd and 4th rounds"
(the user's own framing) is a SERVING-time behavior this model's own
output feeds into -- a live prediction would compare a golfer's own
projected 36-hole cumulative score against this model's projected cut
line before ever calling the round-3/4 models for them. There is no
predict Lambda yet (Phase 5 step 4) to implement that comparison in; this
script only trains the cut-line number itself.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_cutline_model.py
"""
import functools
import logging

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

import pandas as pd

from library.aws.s3_manager import S3Manager
from library.ml import backtest, model_types, training_common
from library.ml import train_regressor_model_common as regressor_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pga-train-model")

SPORT = "pga"
MODEL_NAME = "projected-cut-line"
CUTLINE_FEATURES_KEY = "pga/training-data/cutline_features.parquet"

# cut_count is kept on the dataset for this train-time filter, not as a
# feature input -- a live prediction can't know in advance how many
# players will make a cut that hasn't happened yet, only whether the
# tournament HAS a cut mechanism at all (a property of the tournament
# format, already captured indirectly through is_major/purse/field_size).
NON_FEATURE_COLUMNS = {"event_key", "event_date", "cut_count"}
LABEL_COLUMN = "label_cut_score"

CANDIDATES = model_types.regressor_candidates(include_lightgbm=False)


def _filter_to_real_cut_tournaments(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["cut_count"] > 0].copy()


def _filter_to_scored_rows(df: pd.DataFrame) -> pd.DataFrame:
    """A real cut tournament (cut_count > 0) can still have a null
    label_cut_score -- library/features/pga.py's build_cutline_event_
    features runs it through _as_number(), which coerces a dirty
    non-numeric stored value to None rather than crashing feature-
    engineering's Parquet write. Same drop-before-fit convention train_
    score_model.py's own _filter_to_scored_rows uses -- without it a None
    here reaches y_train/y_test directly and every non-XGBoost candidate
    raises on .fit() (sklearn regressors reject a NaN target)."""
    return df[df[LABEL_COLUMN].notna()].copy()

_feature_columns = functools.partial(training_common.feature_columns, non_feature_columns=NON_FEATURE_COLUMNS)


def train(s3: S3Manager, df: pd.DataFrame) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
    df = _filter_to_real_cut_tournaments(df)
    df = _filter_to_scored_rows(df)
    return regressor_common.train(
        s3, df, SPORT, MODEL_NAME,
        label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS,
        candidates=CANDIDATES, logger=logger,
    )


_script = training_common.TrainingScript(
    globals(), features_key=CUTLINE_FEATURES_KEY, row_noun="tournament", model_name=MODEL_NAME,
)
main = _script.main


if __name__ == "__main__":
    main()
