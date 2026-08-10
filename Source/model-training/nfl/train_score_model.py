"""
NFL game score model training -- one model per score target:
SCORE_TARGET=margin for the game's final margin (home score minus away
score), SCORE_TARGET=home_score or SCORE_TARGET=away_score for each
team's actual final score. Reads the exact same event_features.parquet as
train_win_probability_model.py -- same feature columns, same chronological split -- and
derives whichever label SCORE_TARGET asks for from the already-present
label_home_score/label_away_score columns at training time. No new
feature engineering needed for any of the three.

Deliberately one script for all three rather than one script per target
-- SCORE_TARGET is the only thing that varies, same reasoning as
TARGET_STAT in train_player_prop_model.py. Run a given target by
overriding the SCORE_TARGET environment variable at `aws ecs run-task`
time (see Terraform/ecs-task-nfl-train-score-model.tf and
Terraform/scheduler-nfl-train-score-model.tf, which schedules all three).

Runs every CANDIDATES adapter as a competing candidate against the same
holdout split via library.ml.backtest.run_backtest, and promotes
whichever wins on rmse -- see train_win_probability_model.py's own docstring for the
reasoning behind this roster, which mirrors it (same four algorithms,
this target's regression-capable candidates instead of win-probability's
classification-capable ones).

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    SCORE_TARGET (one of "margin", "home_score", "away_score")
    AWS_REGION

Usage:
    SCORE_TARGET=margin python train_score_model.py
"""
import logging
import os

try:
    # See train_win_probability_model.py's own comment -- must run before
    # any sklearn import (including the one directly below).
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml.model_types import ElasticNetAdapter, MLPRegressorAdapter, RandomForestRegressorAdapter, XGBoostRegressorAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

SPORT = "nfl"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"

# Identifiers, never model inputs -- same event-level dataset and
# exclusion set as train_win_probability_model.py, since this is the same features,
# different label.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id", "venue_city", "venue_state"}
LABEL_COLUMN = "label_score_target"
SUMMARY_METRICS = ["rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae"]
PROMOTION_METRIC = "rmse"

# Each target versions independently (nfl/score-margin/, nfl/home-score/,
# nfl/away-score/) -- explicit names rather than a string transform of
# SCORE_TARGET, since "margin" isn't itself a raw column name the way
# player-prop's TARGET_STAT values are.
MODEL_NAMES = {
    "margin": "score-margin",
    "home_score": "home-score",
    "away_score": "away-score",
}

CANDIDATES = [
    XGBoostRegressorAdapter(),
    ElasticNetAdapter(),
    RandomForestRegressorAdapter(),
    MLPRegressorAdapter(),
]


def _model_name(score_target: str) -> str:
    return MODEL_NAMES[score_target]


def _add_label(df: pd.DataFrame, score_target: str) -> pd.DataFrame:
    df = df.copy()
    if score_target == "margin":
        df[LABEL_COLUMN] = df["label_home_score"] - df["label_away_score"]
    elif score_target == "home_score":
        df[LABEL_COLUMN] = df["label_home_score"]
    elif score_target == "away_score":
        df[LABEL_COLUMN] = df["label_away_score"]
    else:
        raise ValueError(f"Unknown SCORE_TARGET: {score_target!r} (expected margin, home_score, or away_score)")
    return df


def _feature_columns(df: pd.DataFrame) -> list[str]:
    return training_common.feature_columns(df, NON_FEATURE_COLUMNS)


def _column_or_mean(df: pd.DataFrame, column: str) -> pd.Series:
    """Fills a team with no rolling history yet (its own average is
    undefined) with the column's own mean across these rows, rather than
    a fixed number -- a real fallback for a points-scored/allowed rate,
    unlike 0 which would mean "predict a shutout"."""
    return df[column].fillna(df[column].mean())


def _naive_prediction(df: pd.DataFrame, score_target: str) -> pd.Series:
    """A trivial baseline built entirely from each team's own existing
    rolling scoring/allowing averages -- no model at all, just
    recombining features that already exist the way a simple power
    rating would: a team's own scoring rate averaged with its opponent's
    own allowing rate."""
    home_scored = _column_or_mean(df, "home_avg_points_scored")
    home_allowed = _column_or_mean(df, "home_avg_points_allowed")
    away_scored = _column_or_mean(df, "away_avg_points_scored")
    away_allowed = _column_or_mean(df, "away_avg_points_allowed")

    if score_target == "margin":
        return (home_scored - home_allowed) - (away_scored - away_allowed)
    if score_target == "home_score":
        return (home_scored + away_allowed) / 2
    return (away_scored + home_allowed) / 2  # away_score


def train(s3: S3Manager, df: pd.DataFrame, score_target: str) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
    df = _add_label(df, score_target)
    feature_columns = _feature_columns(df)
    train_df, test_df = training_common.chronological_split(df, training_common.TEST_FRACTION)
    train_date_range = [str(train_df["event_date"].min()), str(train_df["event_date"].max())]
    test_date_range = [str(test_df["event_date"].min()), str(test_df["event_date"].max())]
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), *train_date_range, len(test_df), *test_date_range,
    )

    X_train = training_common.numeric_frame(train_df, feature_columns)
    y_train = train_df[LABEL_COLUMN]
    X_test = training_common.numeric_frame(test_df, feature_columns)
    y_test = test_df[LABEL_COLUMN]

    naive_predictions = _naive_prediction(test_df, score_target)
    naive_baseline_metrics = {
        "naive_baseline_rmse": float(root_mean_squared_error(y_test, naive_predictions)),
        "naive_baseline_mae": float(mean_absolute_error(y_test, naive_predictions)),
    }

    return backtest.run_backtest(
        s3, SPORT, _model_name(score_target), task="regression",
        X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
        candidates=CANDIDATES,
        naive_baseline_metrics=naive_baseline_metrics,
        extra_metadata={
            "score_target": score_target,
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "train_date_range": train_date_range,
            "test_date_range": test_date_range,
        },
        summary_metrics=SUMMARY_METRICS,
        promotion_metric=PROMOTION_METRIC,
        run_id=training_common.resolve_run_id(),
    )


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    score_target = os.environ["SCORE_TARGET"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)
    model_name = _model_name(score_target)

    logger.info("Loading %s training data from s3://%s/%s", model_name, bucket, EVENT_FEATURES_KEY)
    df = training_common.load_features(s3, EVENT_FEATURES_KEY)
    logger.info("Loaded %d event rows", len(df))

    train(s3, df, score_target)


if __name__ == "__main__":
    main()
