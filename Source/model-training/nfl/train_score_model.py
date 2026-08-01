"""
NFL game score model training (XGBoost regression) -- one model per
score target: SCORE_TARGET=margin for the game's final margin (home
score minus away score), SCORE_TARGET=home_score or SCORE_TARGET=away_score
for each team's actual final score. Reads the exact same
event_features.parquet as train_model.py -- same feature columns, same
chronological split -- and derives whichever label SCORE_TARGET asks for
from the already-present label_home_score/label_away_score columns at
training time. No new feature engineering needed for any of the three.

Deliberately one script for all three rather than one script per target
-- SCORE_TARGET is the only thing that varies, same reasoning as
TARGET_STAT in train_player_prop_model.py. Run a given target by
overriding the SCORE_TARGET environment variable at `aws ecs run-task`
time (see Terraform/ecs-task-nfl-train-score-model.tf and
Terraform/scheduler-nfl-train-score-model.tf, which schedules all three).

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    SCORE_TARGET (one of "margin", "home_score", "away_score")
    AWS_REGION

Usage:
    SCORE_TARGET=margin python train_score_model.py
"""
import logging
import os

import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit

import model_common
from library.aws.s3_manager import S3Manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"

# Identifiers, never model inputs -- same event-level dataset and
# exclusion set as train_model.py, since this is the same features,
# different label.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id", "venue_city", "venue_state"}
LABEL_COLUMN = "label_score_target"
ALGORITHM = "xgboost"
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

# Same search shape as train_model.py's -- see that file for why
# max_depth has a floor of 2 and why n_jobs is split the way it is below.
PARAM_DISTRIBUTIONS = {
    "max_depth": [2, 3, 4, 5, 6, 7, 8, 9],
    "n_estimators": [50, 100, 200, 300, 400, 450, 500, 550, 600, 750],
    "learning_rate": [0.001, 0.002, 0.003, 0.005, 0.007, 0.01, 0.02, 0.05, 0.1, 0.2],
    "min_child_weight": [1, 3, 5, 7, 10, 15, 20],
    "subsample": [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 0.9, 1.0],
}
SEARCH_ITERATIONS = 300
CV_SPLITS = 8
RANDOM_STATE = 42


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
    return model_common.feature_columns(df, NON_FEATURE_COLUMNS)


_chronological_split = model_common.chronological_split


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


def _tune_hyperparameters(X_train: pd.DataFrame, y_train: pd.Series) -> dict:
    """Same TimeSeriesSplit-over-k-fold reasoning as train_model.py's --
    scored on RMSE instead of log_loss since this predicts a continuous
    score/margin, not a win probability."""
    total_fits = SEARCH_ITERATIONS * CV_SPLITS
    logger.info(
        "Starting hyperparameter search: %d candidates x %d CV folds = %d fits",
        SEARCH_ITERATIONS, CV_SPLITS, total_fits,
    )
    search = RandomizedSearchCV(
        xgb.XGBRegressor(objective="reg:squarederror", n_jobs=1),
        param_distributions=PARAM_DISTRIBUTIONS,
        n_iter=SEARCH_ITERATIONS,
        scoring="neg_root_mean_squared_error",
        cv=TimeSeriesSplit(n_splits=CV_SPLITS),
        random_state=RANDOM_STATE,
        verbose=10,
        n_jobs=-1,
    )
    search.fit(X_train, y_train)
    logger.info(
        "Best hyperparameters: %s (cv rmse=%.4f)",
        search.best_params_, -search.best_score_,
    )
    return search.best_params_


def train(df: pd.DataFrame, score_target: str) -> tuple[xgb.XGBRegressor, dict]:
    df = _add_label(df, score_target)
    feature_columns = _feature_columns(df)
    train_df, test_df = _chronological_split(df, model_common.TEST_FRACTION)
    train_date_range = [str(train_df["event_date"].min()), str(train_df["event_date"].max())]
    test_date_range = [str(test_df["event_date"].min()), str(test_df["event_date"].max())]
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), *train_date_range, len(test_df), *test_date_range,
    )

    X_train = model_common.numeric_frame(train_df, feature_columns)
    y_train = train_df[LABEL_COLUMN]
    X_test = model_common.numeric_frame(test_df, feature_columns)
    y_test = test_df[LABEL_COLUMN]

    best_params = _tune_hyperparameters(X_train, y_train)
    model = xgb.XGBRegressor(objective="reg:squarederror", **best_params)
    model.fit(X_train, y_train)

    raw_importances = model.get_booster().get_score(importance_type="gain")
    feature_importances = dict(sorted(
        ((col, float(raw_importances.get(col, 0.0))) for col in feature_columns),
        key=lambda kv: kv[1], reverse=True,
    ))

    metrics = model_common.evaluate_regression_holdout(model, X_test, y_test)

    naive_predictions = _naive_prediction(test_df, score_target)
    metrics["naive_baseline_rmse"] = float(root_mean_squared_error(y_test, naive_predictions))
    metrics["naive_baseline_mae"] = float(mean_absolute_error(y_test, naive_predictions))

    metrics.update({
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "train_date_range": train_date_range,
        "test_date_range": test_date_range,
    })
    logger.info(
        "Holdout rmse=%.4f mae=%.4f (naive baseline rmse=%.4f mae=%.4f)",
        metrics["rmse"], metrics["mae"], metrics["naive_baseline_rmse"], metrics["naive_baseline_mae"],
    )

    return model, {
        "score_target": score_target,
        "feature_columns": feature_columns,
        "feature_importances": feature_importances,
        "hyperparameters": best_params,
        **metrics,
    }


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    score_target = os.environ["SCORE_TARGET"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)
    model_name = _model_name(score_target)

    logger.info("Loading %s training data from s3://%s/%s", model_name, bucket, EVENT_FEATURES_KEY)
    df = model_common.load_features(s3, EVENT_FEATURES_KEY)
    logger.info("Loaded %d event rows", len(df))

    model, metadata = train(df, score_target)

    model_bytes = model.get_booster().save_raw()
    model_card = model_common.save_model_artifact(
        s3, model_name, ALGORITHM, model_bytes, "model.xgb", metadata, SUMMARY_METRICS,
    )
    model_common.promote_if_better(s3, model_name, model_card["version"], metadata, PROMOTION_METRIC)


if __name__ == "__main__":
    main()
