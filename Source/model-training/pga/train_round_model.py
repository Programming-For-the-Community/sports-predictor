"""
PGA per-round projected-score model training. ROUND_NUMBER=1/2/3/4 for
that round's own score-to-par model, versioned independently under
"round-1"/"round-2"/"round-3"/"round-4" -- one script covers all four
targets, ROUND_NUMBER is the only thing that varies, same SCORE_TARGET-
style single-script-multiple-targets convention NFL's own train_score_
model.py uses for margin/home_score/away_score.

Reads round_features.parquet (round-grain, one row per golfer per round
ACTUALLY PLAYED -- see feature-engineering/pga/build_dataset.py's
build_round_dataset) from S3, filters to this ROUND_NUMBER's own rows,
then runs the same 4-candidate regressor tournament every other sport's
score/ranking model uses via library.ml.backtest.run_backtest, promoted
on rmse.

Rounds 3 and 4 naturally have fewer training rows than rounds 1-2 -- a
cut golfer's own `rounds` list simply has no round-3/4 entry at all (see
library/normalize/pga.py's _parse_rounds), so this script never needs to
special-case "did this golfer make the cut" -- the dataset itself already
reflects it. Whether to even call the round-3/4 model for a given golfer
on a live, in-progress tournament (skipped if they're projected to miss
the cut) is a serving-time concern, handled in
aws-lambdas/pga/predict/event_prediction.py, not a training-time one --
see train_cutline_model.py's own docstring.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION
    ROUND_NUMBER (one of "1", "2", "3", "4")

Usage:
    ROUND_NUMBER=1 python train_round_model.py
"""
import logging
import os

try:
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

import pandas as pd

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml import train_regressor_model_common as regressor_common
from library.ml.model_types import ElasticNetAdapter, MLPRegressorAdapter, RandomForestRegressorAdapter, XGBoostRegressorAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pga-train-model")

SPORT = "pga"
ROUND_FEATURES_KEY = "pga/training-data/round_features.parquet"
VALID_ROUND_NUMBERS = {1, 2, 3, 4}

# round_number is kept on the dataset for this train-time filter, not as
# a feature input -- once filtered to one round, it's a constant column
# that would contribute nothing to split on.
NON_FEATURE_COLUMNS = {"event_key", "entity_id", "event_date", "round_number"}
LABEL_COLUMN = "label_round_score_to_par"

CANDIDATES = [
    XGBoostRegressorAdapter(),
    ElasticNetAdapter(),
    RandomForestRegressorAdapter(),
    MLPRegressorAdapter(),
]


def _resolve_round_number() -> int:
    raw = os.environ["ROUND_NUMBER"]
    try:
        round_number = int(raw)
    except ValueError:
        round_number = -1
    if round_number not in VALID_ROUND_NUMBERS:
        raise ValueError(f"Unknown ROUND_NUMBER: {raw!r} (expected one of {sorted(VALID_ROUND_NUMBERS)})")
    return round_number


def _filter_to_round(df: pd.DataFrame, round_number: int) -> pd.DataFrame:
    return df[df["round_number"] == round_number].copy()


def _filter_to_scored_rows(df: pd.DataFrame) -> pd.DataFrame:
    """A round that was actually played can still have a null
    label_round_score_to_par -- library/features/pga.py's build_round_
    event_features runs it through _as_number(), which coerces any dirty
    non-numeric stored value (e.g. a withdrawal mid-round) to None rather
    than crashing feature-engineering's Parquet write. Same drop-before-
    fit convention train_score_model.py's own _filter_to_scored_rows
    uses -- without it a None here reaches y_train/y_test directly and
    every non-XGBoost candidate raises on .fit() (sklearn regressors
    reject a NaN target)."""
    return df[df[LABEL_COLUMN].notna()].copy()


def _feature_columns(df: pd.DataFrame) -> list[str]:
    return training_common.feature_columns(df, NON_FEATURE_COLUMNS)


def train(s3: S3Manager, df: pd.DataFrame, round_number: int) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
    df = _filter_to_round(df, round_number)
    df = _filter_to_scored_rows(df)
    return regressor_common.train(
        s3, df, SPORT, f"round-{round_number}",
        label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS,
        candidates=CANDIDATES, logger=logger,
        extra_metadata={"round_number": round_number},
    )


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    round_number = _resolve_round_number()
    s3 = S3Manager(bucket, region=region)

    logger.info("Loading round-%d training data from s3://%s/%s", round_number, bucket, ROUND_FEATURES_KEY)
    df = training_common.load_features(s3, ROUND_FEATURES_KEY)
    logger.info("Loaded %d round rows", len(df))

    train(s3, df, round_number)


if __name__ == "__main__":
    main()
