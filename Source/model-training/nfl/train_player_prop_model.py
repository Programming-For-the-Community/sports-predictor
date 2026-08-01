"""
NFL player-prop model training (XGBoost regression) -- one model per
target stat (e.g. TARGET_STAT=passing_yards for QB passing yards,
TARGET_STAT=rushing_yards for RB rushing yards, TARGET_STAT=receptions
for a receiver). Reads player_features.parquet (written by
Source/feature-engineering/nfl/build_dataset.py's build_player_dataset)
from S3, filters to rows where the player actually recorded TARGET_STAT
in the game being labeled AND has an established history of it (see
_filter_to_target_stat), and trains a regressor predicting that value
from their own rolling stat history (see build_player_features and
rolling_player_stat_averages in library/features/nfl.py). Requires a
player_features.parquet built after games_with_<stat> was added to
rolling_player_stat_averages -- re-run feature engineering first if an
older dataset is all that's in S3.

Deliberately one script for every stat rather than one script (or one
Terraform task definition, or one image) per stat -- TARGET_STAT is the
only thing that varies between a passing-yards run and a rushing-yards
run; the dataset, split, evaluation, artifact writing, and promotion are
all identical, and live in model_common.py alongside train_model.py and
train_baseline_model.py. Run a given stat by overriding the TARGET_STAT
environment variable at `aws ecs run-task` time (see
Terraform/ecs-task-nfl-train-player-prop-model.tf) rather than deploying
a new task definition per stat.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    TARGET_STAT (a stat_line key, e.g. "passing_yards", "rushing_yards")
    AWS_REGION

Usage:
    TARGET_STAT=passing_yards python train_player_prop_model.py
"""
import json
import logging
import os

import pandas as pd
import xgboost as xgb
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit

import model_common
from library.aws.s3_manager import S3Manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

PLAYER_FEATURES_KEY = "nfl/training-data/player_features.parquet"

# Identifiers, never model inputs. label_stat_line and label_started are
# excluded generically (they start with "label_"), same as every other
# script here -- LABEL_COLUMN below also starts with "label_" for the
# same reason, so it never leaks into feature_columns either.
NON_FEATURE_COLUMNS = {"event_key", "player_key", "entity_id", "team_id", "opponent_id", "event_date"}
LABEL_COLUMN = "label_target_stat"
ALGORITHM = "xgboost"
SUMMARY_METRICS = ["rmse", "mae"]
PROMOTION_METRIC = "rmse"

# A player must have recorded TARGET_STAT in at least this many of their
# own windowed prior games, not just the game being labeled -- excludes
# one-off gadget plays (e.g. a WR's single career pass attempt) whose
# rolling avg_<stat> would otherwise be undefined/NaN anyway. A real
# starter or regular backup at the relevant position trivially clears
# this; it's the fluke rows this is meant to catch. Left at a low bar
# rather than raised further -- MIN_AVG_FRACTION_OF_MEDIAN below is what
# catches a player who clears this count but at trivial volume, so this
# constant doesn't also need to do that job (and raising it would cost
# legitimate new starters with only a couple of games of history).
MIN_PRIOR_GAMES_WITH_STAT = 2

# A player can pass the games_with_<stat> bar above by recurring at low
# volume rather than one-off (e.g. a WR who's run a real wildcat passing
# package in a couple of recent games while remaining primarily a
# receiver) -- their own avg_<stat> stays far below what a real
# participant in this stat category posts. Scale-invariant relative to
# the stat's own filtered population median rather than an absolute
# floor, so the same fraction applies whether TARGET_STAT is yards
# (large, continuous) or something like sacks (small, discrete) without
# per-stat calibration.
MIN_AVG_FRACTION_OF_MEDIAN = 0.35

# Same search shape as train_model.py's -- see that file for why
# max_depth has a floor of 2 (a depth-1 stump can't model any feature
# interaction) and why n_jobs is split the way it is below.
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


def _model_name(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def _filter_to_target_stat(df: pd.DataFrame, target_stat: str) -> pd.DataFrame:
    """Not every player-game recorded target_stat (a kicker never has
    passing_yards) -- label_stat_line is JSON-encoded (see
    build_player_features in library/features/nfl.py, and _write_parquet
    in feature-engineering/nfl/build_dataset.py for why), so it has to be
    parsed before it can be filtered on or turned into a label.

    Also requires MIN_PRIOR_GAMES_WITH_STAT prior games with this stat,
    via the games_with_<stat> column (rolling_player_stat_averages in
    library/features/nfl.py). Without this, a one-off gadget-play row
    (e.g. a WR's single career pass attempt) still passes the has_stat
    check above, and its avg_<other-position>_* features -- genuinely
    populated from that player's real history, unlike the near-universal
    NaN they'd be for an actual QB -- give a shallow tree an easy way to
    isolate that single fluke label instead of learning real QB
    performance patterns. A real starter or regular backup trivially
    clears this bar every week; a true one-off doesn't.

    Also requires avg_<stat> to be at least MIN_AVG_FRACTION_OF_MEDIAN of
    the remaining population's own median avg_<stat> -- catches a player
    who clears the games_with_<stat> bar by recurring at trivial volume
    rather than by being a true one-off (see MIN_AVG_FRACTION_OF_MEDIAN).
    The median is computed AFTER the games_with_<stat> filter above, not
    before -- otherwise the one-off flukes that filter excludes would
    still be dragging the median down and weakening this threshold.
    """
    stat_lines = df["label_stat_line"].apply(json.loads)
    has_stat = stat_lines.apply(lambda stat_line: target_stat in stat_line)
    filtered = df[has_stat].copy()
    filtered[LABEL_COLUMN] = stat_lines[has_stat].apply(lambda stat_line: float(stat_line[target_stat]))

    volume_column = f"games_with_{target_stat}"
    filtered = filtered[filtered[volume_column] >= MIN_PRIOR_GAMES_WITH_STAT]

    avg_column = f"avg_{target_stat}"
    median_avg = filtered[avg_column].median()
    filtered = filtered[filtered[avg_column] >= median_avg * MIN_AVG_FRACTION_OF_MEDIAN]

    return filtered


def _feature_columns(df: pd.DataFrame) -> list[str]:
    """player_features.parquet has ONE schema spanning every position in
    the league (build_dataset.py's _write_parquet unions every row's
    columns) -- after _filter_to_target_stat narrows to one stat's real
    population, most other positions' columns (kicking, punting,
    defensive, ...) are structurally inapplicable to every remaining row,
    not just occasionally sparse the way win-probability's
    weather_temperature is. Dropping all-null columns here, scoped to
    this script rather than model_common.feature_columns, keeps the
    other two scripts' feature schema stable regardless of a given week's
    null pattern -- see model_common.feature_columns' own callers."""
    candidates = model_common.feature_columns(df, NON_FEATURE_COLUMNS)
    return [col for col in candidates if df[col].notna().any()]


_chronological_split = model_common.chronological_split


def _tune_hyperparameters(X_train: pd.DataFrame, y_train: pd.Series) -> dict:
    """Same TimeSeriesSplit-over-k-fold reasoning as train_model.py's --
    scored on RMSE instead of log_loss since this predicts a continuous
    stat, not a win probability."""
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


def train(df: pd.DataFrame, target_stat: str) -> tuple[xgb.XGBRegressor, dict]:
    df = _filter_to_target_stat(df, target_stat)
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
    metrics.update({
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "train_date_range": train_date_range,
        "test_date_range": test_date_range,
    })
    logger.info("Holdout rmse=%.4f mae=%.4f", metrics["rmse"], metrics["mae"])

    return model, {
        "target_stat": target_stat,
        "feature_columns": feature_columns,
        "feature_importances": feature_importances,
        "hyperparameters": best_params,
        **metrics,
    }


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    target_stat = os.environ["TARGET_STAT"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)
    model_name = _model_name(target_stat)

    logger.info("Loading %s training data from s3://%s/%s", model_name, bucket, PLAYER_FEATURES_KEY)
    df = model_common.load_features(s3, PLAYER_FEATURES_KEY)
    logger.info("Loaded %d player-game rows", len(df))

    model, metadata = train(df, target_stat)

    model_bytes = model.get_booster().save_raw()
    model_card = model_common.save_model_artifact(
        s3, model_name, ALGORITHM, model_bytes, "model.xgb", metadata, SUMMARY_METRICS,
    )
    model_common.promote_if_better(s3, model_name, model_card["version"], metadata, PROMOTION_METRIC)


if __name__ == "__main__":
    main()
